"""Media resolution: links, queries and stream backends.

The platform deliberately does **not** shell out to ``yt-dlp``/``ffmpeg`` inside a
request path – the sandbox has neither, and spawning an unpinned media binary
from a plugin is exactly the kind of thing the capability gates exist for.  So
resolution happens in layers, in this order:

1. **direct links** – a URL whose path already points at media, a Telegram file,
   or a ``t.me`` post we can copy (no network call, fully verifiable here),
2. **catalogue search** – the indexed library that ``music`` maintains
   (:class:`meiko.plugins.builtin.music.MusicLibrary`),
3. **remote search** – an optional owner-configured extractor endpoint
   (``resolve_endpoint``, requires ``http.outbound``), which is what a real
   deployment points at its own yt-dlp/relay service,
4. **voice-chat delivery** – requires an MTProto client (pyrogram/pytgcalls) and
   ``ffmpeg``; :func:`vc_available` reports the truth instead of pretending.

Every layer reports what it did (:class:`ResolveOutcome`), so the player can tell
the user "queued as an audio file" rather than silently doing nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from .queue import Track

AUDIO_EXT = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac", ".alac", ".mka"}
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts", ".flv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
QUALITY_FLAGS = {
    "-sd": 480, "--480p": 480,
    "-hd": 720, "--720p": 720,
    "-fhd": 1080, "--1080p": 1080,
    "-2k": 1440, "-1440p": 1440, "--1440p": 1440,
    "-4k": 2160, "-2160p": 2160, "--2160p": 2160,
}
PREFER_NOW_FLAGS = {"-f", "--force", "-now"}
QUALITY_LABELS = {480: "480p", 720: "720p", 1080: "1080p", 1440: "1440p", 2160: "2160p"}


@dataclass
class PlayRequest:
    """A parsed ``/play`` / ``/vplay`` argument."""

    query: str = ""
    url: str = ""
    video: bool = False
    play_now: bool = False
    height: int = 0  # requested video quality, 0 = best
    save: bool = False  # keep a copy (``-s``)


@dataclass
class ResolveOutcome:
    tracks: list[Track] = field(default_factory=list)
    layer: str = "none"  # direct | catalogue | remote | none
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.tracks)

    @property
    def first(self) -> Track | None:
        return self.tracks[0] if self.tracks else None


def split_flags(text: str) -> tuple[PlayRequest, list[str]]:
    """Pull ``-f``/``-hd``/``-s`` style flags out of an argument string."""
    request = PlayRequest()
    leftovers: list[str] = []
    for token in (text or "").split():
        lowered = token.lower()
        if lowered in PREFER_NOW_FLAGS:
            request.play_now = True
        elif lowered in QUALITY_FLAGS:
            request.height = QUALITY_FLAGS[lowered]
            request.video = True
        elif lowered in ("-best", "--best"):
            request.height = 0
            request.video = True
        elif lowered in ("-s", "--save"):
            request.save = True
        else:
            leftovers.append(token)
    remainder = " ".join(leftovers).strip()
    return request, [remainder] if remainder else []


def parse_request(text: str, *, video: bool = False) -> PlayRequest:
    request, parts = split_flags(text)
    request.video = request.video or video
    argument = parts[0] if parts else ""
    request.query = argument
    if looks_like_url(argument):
        request.url = argument
    return request


def looks_like_url(text: str) -> bool:
    if not text:
        return False
    if re.match(r"^(?:https?|tg)://", text, re.I):
        return True
    return bool(re.match(r"^(?:www\.)?t\.me/", text, re.I))


def youtube_id(url: str) -> str:
    """Extract the video id from every YouTube URL shape we accept."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0][:20]
    if host in YOUTUBE_HOSTS:
        if parsed.path.startswith("/watch"):
            return (parse_qs(parsed.query).get("v") or [""])[0][:20]
        for prefix in ("/shorts/", "/embed/", "/live/", "/v/"):
            if parsed.path.startswith(prefix):
                return parsed.path[len(prefix) :].split("/")[0][:20]
    return ""


def direct_media_kind(url: str) -> str:
    """``audio``/``video``/``image``/``""`` for a URL that *is* a media file."""
    try:
        path = urlparse(url).path.lower()
    except ValueError:
        return ""
    suffix = "." + path.rsplit(".", 1)[-1] if "." in path else ""
    if suffix in AUDIO_EXT:
        return "audio"
    if suffix in VIDEO_EXT:
        return "video"
    if suffix in IMAGE_EXT:
        return "image"
    return ""


def track_from_url(url: str, *, video: bool = False, height: int = 0, requested_by: str = "") -> Track:
    """A track for a URL we can play without an extractor."""
    kind = direct_media_kind(url) or ("video" if video else "audio")
    identifier = youtube_id(url)
    name = urlparse(url).path.rsplit("/", 1)[-1] or urlparse(url).netloc or url
    if identifier:
        name = f"YouTube · {identifier}"
    return Track(
        title=name,
        source=url,
        url=url,
        kind=kind,
        video_id=identifier,
        height=height,
        thumbnail=f"https://i.ytimg.com/vi/{identifier}/hqdefault.jpg" if identifier else "",
        requested_by=requested_by,
        stream_url=url if direct_media_kind(url) else "",
    )


def track_from_telegram(file_id: str, *, title: str = "", kind: str = "audio", duration: int = 0) -> Track:
    return Track(
        title=title or "Telegram file",
        file_id=file_id,
        source=file_id,
        kind=kind,
        duration=max(0, duration),
        stream_url="",
    )


class SearchBackend(Protocol):
    """Anything that can turn a query into tracks (catalogue, remote API…)."""

    async def search(self, query: str, limit: int) -> list[Track]:  # pragma: no cover
        ...


class CatalogueBackend:
    """Search backend over the local ``music`` library."""

    def __init__(self, library: Any) -> None:
        self.library = library

    async def search(self, query: str, limit: int) -> list[Track]:
        results = self.library.search(query, limit=limit)
        tracks = []
        for entry, _score in results:
            tracks.append(
                Track(
                    title=entry.title or entry.filename,
                    uploader=entry.artist,
                    kind="audio",
                    file_id=entry.file_id,
                    source=entry.file_id or entry.filename,
                    url="",
                    requested_by="",
                )
            )
        return tracks


class RemoteBackend:
    """Owner-configured extractor endpoint (e.g. a yt-dlp or relay service)."""

    def __init__(self, endpoint: str, token: str = "", timeout: float = 12.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout = timeout

    async def search(self, query: str, limit: int) -> list[Track]:
        import httpx

        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        params = {"q": query, "limit": limit}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.endpoint}/search", params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("results") if isinstance(payload, dict) else payload
        tracks: list[Track] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            tracks.append(
                Track(
                    title=str(row.get("title") or "unknown"),
                    source=str(row.get("url") or ""),
                    url=str(row.get("url") or ""),
                    kind="video" if row.get("video") else "audio",
                    duration=int(row.get("duration") or 0),
                    uploader=str(row.get("uploader") or ""),
                    thumbnail=str(row.get("thumbnail") or ""),
                    video_id=str(row.get("id") or ""),
                    stream_url=str(row.get("stream_url") or ""),
                    height=int(row.get("height") or 0),
                )
            )
        return tracks[:limit]


def vc_available() -> bool:
    """Can this deployment actually stream into a Telegram voice chat?

    Needs an MTProto client (pyrogram/pytgcalls) *and* ``ffmpeg``.  The check is
    honest on purpose: a player that claims to join a call and then does nothing
    is worse than one that explains what to install.
    """
    import importlib.util
    import shutil

    has_client = any(
        importlib.util.find_spec(name) is not None for name in ("pytgcalls", "pyrogram")
    )
    has_ffmpeg = shutil.which("ffmpeg") is not None
    return bool(has_client and has_ffmpeg)


def missing_vc_requirements() -> list[str]:
    import importlib.util
    import shutil

    missing = []
    if all(importlib.util.find_spec(name) is None for name in ("pytgcalls", "pyrogram")):
        missing.append("pytgcalls (or pyrogram)")
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg")
    return missing


def quality_label(height: int) -> str:
    if not height:
        return "best"
    return QUALITY_LABELS.get(height, f"{height}p")


class MediaResolver:
    """Layered resolution: direct → catalogue → remote."""

    def __init__(
        self,
        *,
        catalogue: SearchBackend | None = None,
        remote: SearchBackend | None = None,
        limit: int = 8,
    ) -> None:
        self.catalogue = catalogue
        self.remote = remote
        self.limit = limit

    async def resolve(self, request: PlayRequest, *, requested_by: str = "") -> ResolveOutcome:
        if request.url:
            if request.url.startswith("tg://") or "t.me/" in request.url:
                return ResolveOutcome(layer="link", reason="telegram link – resolve with /save or reply a file")
            track = track_from_url(
                request.url,
                video=request.video,
                height=request.height,
                requested_by=requested_by,
            )
            if track.kind == "image":
                return ResolveOutcome(layer="direct", reason="that is an image, not playable media")
            return ResolveOutcome(tracks=[track], layer="direct")

        if not request.query:
            return ResolveOutcome(reason="nothing to search for")

        if self.catalogue is not None:
            hits = await self.catalogue.search(request.query, self.limit)
            if hits:
                for track in hits:
                    track.requested_by = requested_by
                    if request.video:
                        track.kind = "video"
                return ResolveOutcome(tracks=hits, layer="catalogue")

        if self.remote is not None:
            try:
                hits = await self.remote.search(request.query, self.limit)
            except Exception as exc:  # pragma: no cover - network failures
                return ResolveOutcome(layer="remote", reason=f"extractor error: {exc}")
            if hits:
                for track in hits:
                    track.requested_by = requested_by
                    if request.video:
                        track.kind = "video"
                    if request.height:
                        track.height = request.height
                return ResolveOutcome(tracks=hits, layer="remote")
            return ResolveOutcome(layer="remote", reason="no results")

        return ResolveOutcome(reason="no source configured (catalogue empty, extractor unset)")


__all__ = [
    "AUDIO_EXT",
    "CatalogueBackend",
    "MediaResolver",
    "PlayRequest",
    "QUALITY_FLAGS",
    "QUALITY_LABELS",
    "RemoteBackend",
    "ResolveOutcome",
    "SearchBackend",
    "Track",
    "VIDEO_EXT",
    "direct_media_kind",
    "looks_like_url",
    "missing_vc_requirements",
    "parse_request",
    "quality_label",
    "split_flags",
    "track_from_telegram",
    "track_from_url",
    "vc_available",
    "youtube_id",
]
