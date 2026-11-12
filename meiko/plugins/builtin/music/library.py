"""Local music index: metadata guessing, fuzzy search and artwork rendering.

Deliberately *no* network source: a plugin that silently downloads from the web
is a policy decision the owner must make explicitly (the ``http.outbound``
permission), so search runs over what the bot has already indexed locally, and
the fetch helpers are opt-in through ``fetch_remote`` in the manifest.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass
from html import escape

from .id3 import TrackTags

FIELD_SEPARATORS = re.compile(r"\s+[-–]\s+")
TRACK_PREFIX = re.compile(r"^\s*\d{1,3}[._)\- ]\s*")
BRACKETS = re.compile(r"[\[(].*?[\])]")
SUSPICIOUS = re.compile(r"\b(official|video|lyrics?|audio|hd|hq|320kbps|128kbps|mv)\b", re.I)


def guess_tags_from_filename(filename: str) -> TrackTags:
    """``03 - Artist - Title [320kbps].mp3`` → artist/title/track."""
    stem = filename.rsplit(".", 1)[0]
    stem = BRACKETS.sub(" ", stem)
    track = ""
    track_match = TRACK_PREFIX.match(stem)
    if track_match:
        track = str(int(re.match(r"\s*(\d{1,3})", stem).group(1)))  # type: ignore[union-attr]
        stem = stem[track_match.end() :].strip(" -–_.")
    parts = [part.strip() for part in FIELD_SEPARATORS.split(stem) if part.strip()]
    if len(parts) >= 2:
        artist, title = parts[0], " - ".join(parts[1:])
    else:
        chunks = [chunk.strip() for chunk in stem.split(" - ") if chunk.strip()]
        if len(chunks) >= 2:
            artist, title = chunks[0], " - ".join(chunks[1:])
        else:
            artist, title = "", SUSPICIOUS.sub("", stem).strip()
    return TrackTags(
        title=title or stem,
        artist=artist,
        track=track,
        genre="",
    )


@dataclass
class LibraryEntry:
    filename: str
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    size: int = 0
    message_id: int = 0
    chat: str = ""
    file_id: str = ""

    @classmethod
    def from_track(cls, filename: str, tags: TrackTags, **extra: object) -> "LibraryEntry":
        return cls(
            filename=filename,
            title=tags.title or filename.rsplit(".", 1)[0],
            artist=tags.artist,
            album=tags.album,
            genre=tags.genre,
            size=int(extra.get("size", 0) or 0),
            message_id=int(extra.get("message_id", 0) or 0),
            chat=str(extra.get("chat", "") or ""),
            file_id=str(extra.get("file_id", "") or ""),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "genre": self.genre,
            "size": self.size,
            "message_id": self.message_id,
            "chat": self.chat,
            "file_id": self.file_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "LibraryEntry":
        return cls(
            filename=str(data.get("filename", "")),
            title=str(data.get("title", "")),
            artist=str(data.get("artist", "")),
            album=str(data.get("album", "")),
            genre=str(data.get("genre", "")),
            size=int(data.get("size", 0) or 0),
            message_id=int(data.get("message_id", 0) or 0),
            chat=str(data.get("chat", "")),
            file_id=str(data.get("file_id", "")),
        )

    @property
    def label(self) -> str:
        parts = [self.title]
        if self.artist:
            parts.append(f"— {self.artist}")
        return " ".join(part for part in parts if part).strip() or self.filename

    @property
    def searchable(self) -> str:
        return " ".join(
            (self.title, self.artist, self.album, self.genre, self.filename)
        ).lower()


class MusicLibrary:
    """Searchable, de-duplicated index of audio files."""

    def __init__(self, entries: list[dict[str, object]] | None = None, limit: int = 3000) -> None:
        self.limit = limit
        self._entries: list[LibraryEntry] = []
        self._filenames: set[str] = set()
        for raw in entries or []:
            entry = LibraryEntry.from_dict(raw)
            if entry.filename in self._filenames:
                continue
            self._entries.append(entry)
            self._filenames.add(entry.filename)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[LibraryEntry]:
        return list(self._entries)

    def add(self, entry: LibraryEntry) -> bool:
        if entry.filename in self._filenames:
            return False
        self._entries.insert(0, entry)
        self._filenames.add(entry.filename)
        del self._entries[self.limit :]
        return True

    def remove(self, filename: str) -> bool:
        before = len(self._entries)
        self._entries = [entry for entry in self._entries if entry.filename != filename]
        self._filenames.discard(filename)
        return len(self._entries) != before

    def get(self, filename: str) -> LibraryEntry | None:
        for entry in self._entries:
            if entry.filename == filename:
                return entry
        return None

    def search(self, query: str, limit: int = 8) -> list[tuple[LibraryEntry, float]]:
        needle = (query or "").strip().lower()
        if not needle:
            return [(entry, 1.0) for entry in self._entries[:limit]]
        scored: list[tuple[LibraryEntry, float]] = []
        for entry in self._entries:
            haystack = entry.searchable
            if needle in haystack:
                score = 0.95 + min(0.05, len(needle) / max(1, len(haystack)))
            else:
                score = difflib.SequenceMatcher(None, needle, haystack).ratio()
                token_hits = sum(1 for token in needle.split() if token in haystack)
                if token_hits:
                    score = max(score, 0.55 + 0.1 * token_hits)
            if score >= 0.45:
                scored.append((entry, round(score, 3)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def stats(self) -> dict[str, int]:
        artists = {entry.artist for entry in self._entries if entry.artist}
        albums = {entry.album for entry in self._entries if entry.album}
        return {
            "tracks": len(self._entries),
            "artists": len(artists),
            "albums": len(albums),
            "bytes": sum(entry.size for entry in self._entries),
        }

    def to_list(self) -> list[dict[str, object]]:
        return [entry.to_dict() for entry in self._entries]


def render_cover_text(tags: TrackTags) -> str:
    title = tags.title or "Unknown title"
    artist = tags.artist or "Unknown artist"
    album = tags.album
    return f"{title}\n{artist}" + (f"\n{album}" if album else "")


def placeholder_cover(text: str, size: int = 320) -> bytes:
    """Draw a gradient cover with Pillow when available, else a tiny valid PNG."""
    try:  # Pillow is optional: the plugin degrades to a solid 1x1 PNG
        from PIL import Image, ImageDraw  # type: ignore[import-not-found]
    except Exception:
        return _solid_png(16, 16, (18, 20, 33))
    image = Image.new("RGB", (size, size), (12, 14, 24))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        ratio = y / max(1, size - 1)
        draw.line(
            [(0, y), (size, y)],
            fill=(
                int(28 + 60 * ratio),
                int(16 + 30 * math.sin(ratio * math.pi)),
                int(64 + 90 * (1 - ratio)),
            ),
        )
    lines = [line.strip() for line in text.splitlines() if line.strip()][:3]
    step = size // (len(lines) + 1) if lines else size // 2
    for index, line in enumerate(lines):
        y = step * (index + 1) - 6
        draw.text((18, y), escape(line)[:22], fill=(240, 240, 255))
    return _png_bytes(image)


def _png_bytes(image) -> bytes:  # pragma: no cover - needs Pillow
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


__all__ = [
    "LibraryEntry",
    "MusicLibrary",
    "guess_tags_from_filename",
    "placeholder_cover",
    "render_cover_text",
]
