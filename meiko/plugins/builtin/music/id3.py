"""Minimal, dependency-free ID3v2 tag reader/writer.

``mutagen`` is not available in the platform runtime (and pulling a C-extension
audio library into a bot plugin is a supply-chain risk), so the small subset we
actually need – title/artist/album/genre/year/track plus embedded artwork – is
implemented here against ID3v2.4 (UTF-8 text frames).

The writer always *strips* an existing ID3v2 tag first, so tagging twice does not
grow the file or duplicate frames, and it never touches the MPEG audio frames.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

ID3_HEADER = b"ID3"
TEXT_ENCODING = 3  # UTF-8
FRAME_IDS = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "genre": "TCON",
    "year": "TDRC",
    "track": "TRCK",
    "album_artist": "TPE2",
    "comment": "COMM",
}
REVERSE = {value: key for key, value in FRAME_IDS.items()}
READABLE = ("title", "artist", "album", "genre", "year", "track")
MIME_GUESS = {b"jpeg": "image/jpeg", b"JPG": "image/jpeg", b"\x89PNG": "image/png"}


@dataclass
class TrackTags:
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    year: str = ""
    track: str = ""

    def as_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in READABLE}

    @property
    def empty(self) -> bool:
        return not any(self.as_dict().values())

    def summary_rows(self) -> list[tuple[str, str]]:
        return [(name.title(), value) for name, value in self.as_dict().items() if value]


@dataclass
class TagResult:
    tags: TrackTags = field(default_factory=TrackTags)
    artwork_applied: bool = False
    stripped_bytes: int = 0


def _syncsafe(value: int) -> bytes:
    return bytes(
        ((value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F)
    )


def unsyncsafe(data: bytes) -> int:
    total = 0
    for byte in data[:4]:
        total = (total << 7) | (byte & 0x7F)
    return total


def read_tag_size(data: bytes, offset: int = 0) -> int:
    """Total tag byte size (header + frames), 0 when no tag is present."""
    if len(data) < offset + 10 or data[offset : offset + 3] != ID3_HEADER:
        return 0
    body = unsyncsafe(data[offset + 6 : offset + 10])
    footer = 10 if data[offset + 5] & 0x10 else 0
    return 10 + body + footer


def strip_existing(data: bytes) -> tuple[bytes, int]:
    """Return ``(audio_only_bytes, removed_tag_size)``."""
    size = read_tag_size(data)
    if not size:
        return data, 0
    return data[size:], size


def _text_frame(frame_id: str, value: str) -> bytes:
    payload = bytes([TEXT_ENCODING]) + value.encode("utf-8") + b"\x00"
    return frame_id.encode("ascii") + _syncsafe(len(payload)) + b"\x00\x00" + payload


def _picture_frame(artwork: bytes, mime: str = "image/jpeg", description: str = "Cover") -> bytes:
    payload = (
        bytes([TEXT_ENCODING])
        + mime.encode("ascii")
        + b"\x00"
        + bytes([0x03])
        + description.encode("utf-8")
        + b"\x00"
        + artwork
    )
    return b"APIC" + _syncsafe(len(payload)) + b"\x00\x00" + payload


def guess_mime(artwork: bytes) -> str:
    if artwork[:2] == b"\xff\xd8":
        return "image/jpeg"
    if artwork[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


def build_tag(tags: TrackTags, artwork: bytes | None = None) -> bytes:
    frames = b""
    for name, frame_id in FRAME_IDS.items():
        value = getattr(tags, name, "") or ""
        if value:
            frames += _text_frame(frame_id, value)
    if artwork:
        frames += _picture_frame(artwork, guess_mime(artwork))
    if not frames:
        return b""
    header = ID3_HEADER + bytes([4, 0, 0]) + _syncsafe(len(frames))
    return header + frames


def write_tag(data: bytes, tags: TrackTags, artwork: bytes | None = None) -> tuple[bytes, TagResult]:
    """Strip any existing tag and prepend a fresh one. Returns new bytes + report."""
    audio, removed = strip_existing(data)
    tag = build_tag(tags, artwork)
    return tag + audio, TagResult(tags=tags, artwork_applied=bool(artwork), stripped_bytes=removed)


def decode_text(payload: bytes) -> str:
    if not payload:
        return ""
    encoding = payload[0]
    body = payload[1:]
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(encoding, "utf-8")
    try:
        return body.decode(codec, errors="ignore").strip("\x00").strip()
    except Exception:  # pragma: no cover - defensive
        return ""


def read_tags(data: bytes) -> TrackTags:
    """Parse the common text frames of the first ID3v2 tag in ``data``."""
    tags = TrackTags()
    size = read_tag_size(data)
    if not size:
        return tags
    body = data[10:size]
    position = 0
    while position + 10 <= len(body):
        frame_id = body[position : position + 4]
        if not frame_id.strip(b"\x00"):
            break
        if data[3] == 4:
            frame_size = unsyncsafe(body[position + 4 : position + 8])
        else:
            frame_size = struct.unpack(">I", body[position + 4 : position + 8])[0]
        payload = body[position + 10 : position + 10 + frame_size]
        name = REVERSE.get(frame_id.decode("latin-1", errors="ignore"))
        if name and name in READABLE:
            setattr(tags, name, decode_text(payload))
        position += 10 + frame_size
        if frame_size <= 0:
            break
    return tags


def is_audio(filename: str) -> bool:
    return filename.lower().rsplit(".", 1)[-1] in {"mp3", "m4a", "flac", "ogg", "opus", "wav", "aac"}


__all__ = [
    "FRAME_IDS",
    "TrackTags",
    "TagResult",
    "build_tag",
    "guess_mime",
    "is_audio",
    "read_tags",
    "read_tag_size",
    "strip_existing",
    "write_tag",
]
