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
   deployment points at its own relay service, and
3b. **local extraction** – :class:`YtDlpBackend`, used when the ``yt-dlp``
   package is importable. It is searched *last* so an operator's own relay wins,
   and it never runs inside the event loop (search and extraction happen in a
   worker thread).
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


class YtDlpBackend:
    """Search and resolve through ``yt-dlp`` when it is installed.

    ``yt-dlp`` is an optional dependency: the plugin must behave identically
    (minus this layer) when it is absent, so every entry point checks
    :meth:`available` and the failure carries the install hint.
    """

    name = "yt-dlp"

    def __init__(self, *, max_results: int = 8, timeout: float = 20.0) -> None:
        self.max_results = max_results
        self.timeout = timeout

    @staticmethod
    def module():
        import importlib

        try:
            return importlib.import_module("yt_dlp")
        except Exception:  # optional dependency
            return None

    def available(self) -> bool:
        return self.module() is not None

    async def search(self, query: str, limit: int) -> list[Track]:
        module = self.module()
        if module is None:
            raise RuntimeError("yt-dlp is not installed (pip install yt-dlp)")
        import asyncio

        wanted = max(1, min(int(limit or self.max_results), 25))

        def run() -> list[Track]:
            options = {
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "skip_download": True,
                "extract_flat": "in_playlist",
                "noplaylist": True,
                "socket_timeout": self.timeout,
            }
            with module.YoutubeDL(options) as ydl:
                info = ydl.extract_info(f"ytsearch{wanted}:{query}", download=False) or {}
            tracks: list[Track] = []
            for entry in (info.get("entries") or [])[:wanted]:
                if not isinstance(entry, dict):
                    continue
                identifier = str(entry.get("id") or "")
                url = str(entry.get("webpage_url") or entry.get("url") or "")
                if not identifier and not url:
                    continue
                tracks.append(
                    Track(
                        title=str(entry.get("title") or identifier or url),
                        source=url,
                        url=url,
                        kind="audio",
                        duration=int(entry.get("duration") or 0),
                        uploader=str(entry.get("uploader") or entry.get("channel") or ""),
                        thumbnail=str(entry.get("thumbnail") or ""),
                        video_id=identifier,
                    )
                )
            return tracks

        import asyncio as _asyncio

        return await _asyncio.to_thread(run)

    async def resolve(self, url: str, *, video: bool = False, height: int = 0) -> Track | None:
        """A playable track (with a short-lived ``stream_url``) for one page URL."""
        module = self.module()
        if module is None:
            return None
        import asyncio

        def run() -> Track | None:
            options = {
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "skip_download": True,
                "format": (
                    f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={height}]"
                    if video and height
                    else "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"
                    if video
                    else "bestaudio[ext=m4a]/bestaudio/best"
                ),
            }
            with module.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False) or {}
            stream = str(info.get("url") or "")
            if not stream:
                for fmt in info.get("formats") or []:
                    candidate = str(fmt.get("url") or "")
                    if candidate and fmt.get("acodec") not in (None, "none"):
                        stream = candidate
                        break
            if not stream:
                return None
            identifier = str(info.get("id") or youtube_id(url))
            return Track(
                title=str(info.get("title") or identifier or url),
                source=url,
                url=url,
                kind="video" if video else "audio",
                duration=int(info.get("duration") or 0),
                uploader=str(info.get("uploader") or info.get("channel") or ""),
                thumbnail=str(info.get("thumbnail") or ""),
                video_id=identifier,
                height=int(info.get("height") or height or 0),
                stream_url=stream,
            )

        try:
            return await asyncio.to_thread(run)
        except Exception:  # extraction failures are expected for some URLs
            return None


def vc_available() -> bool:
    """Can this deployment actually stream into a Telegram voice chat?

    Needs an MTProto client (pyrogram/pytgcalls) *and* ``ffmpeg``.  The check is
    honest on purpose: a player that claims to join a call and then does nothing
    is worse than one that explains what to install.
    """
    import importlib.util

    from meiko.modules import toolchain

    has_client = any(
        importlib.util.find_spec(name) is not None for name in ("pytgcalls", "pyrogram")
    )
    return bool(has_client and toolchain.has("ffmpeg"))


def missing_vc_requirements() -> list[str]:
    import importlib.util

    from meiko.modules import toolchain

    missing = []
    if all(importlib.util.find_spec(name) is None for name in ("pytgcalls", "pyrogram")):
        missing.append("pytgcalls (or pyrogram)")
    if not toolchain.has("ffmpeg"):
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
        extractor: YtDlpBackend | None = None,
        limit: int = 8,
    ) -> None:
        self.catalogue = catalogue
        self.remote = remote
        self.extractor = extractor
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
            if not track.stream_url and self.extractor is not None:
                # a page link (YouTube, SoundCloud…) needs extraction before it
                # can be played; the direct-link track stays if that fails
                resolved = await self.extractor.resolve(
                    request.url, video=request.video, height=request.height
                )
                if resolved is not None:
                    resolved.requested_by = requested_by
                    track = resolved
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

        if self.extractor is not None:
            try:
                hits = await self.extractor.search(request.query, self.limit)
            except Exception as exc:
                return ResolveOutcome(layer="extract", reason=f"yt-dlp error: {exc}")
            if hits:
                for track in hits:
                    track.requested_by = requested_by
                    if request.video:
                        track.kind = "video"
                    if request.height:
                        track.height = request.height
                return ResolveOutcome(tracks=hits, layer="extract")

        return ResolveOutcome(reason="no source configured (catalogue empty, extractor unset)")


__all__ = [
    "AUDIO_EXT",
    "Playlist",
    "PlaylistManager",
    "SITES",
    "parse_range",
    "site_search_url",
    "split_site",
    "CatalogueBackend",
    "MediaResolver",
    "PlayRequest",
    "QUALITY_FLAGS",
    "QUALITY_LABELS",
    "RemoteBackend",
    "ResolveOutcome",
    "YtDlpBackend",
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


# --------------------------------------------------------------- playlists
@dataclass
class Playlist:
    """A named, ordered list of tracks (a ``/queue save`` snapshot)."""

    name: str
    tracks: list[Track] = field(default_factory=list)
    owner_id: int = 0
    created_at: int = 0
    chart: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tracks": [track.to_dict() for track in self.tracks],
            "owner_id": self.owner_id,
            "created_at": self.created_at,
            "chart": self.chart,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Playlist":
        row = data if isinstance(data, dict) else {}
        return cls(
            name=str(row.get("name") or "playlist"),
            tracks=[Track.from_dict(t) for t in row.get("tracks") or [] if isinstance(t, dict)],
            owner_id=int(row.get("owner_id") or 0),
            created_at=int(row.get("created_at") or 0),
            chart=bool(row.get("chart")),
        )


#: ``site`` shorthand → a resolver/back-end hint the UI can show and a link to open.
SITES: dict[str, str] = {
    "yt": "https://music.youtube.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "sp": "https://open.spotify.com/search/{q}",
    "spotify": "https://open.spotify.com/search/{q}",
    "am": "https://music.apple.com/us/search?term={q}",
    "apple": "https://music.apple.com/us/search?term={q}",
    "sc": "https://soundcloud.com/search?q={q}",
    "soundcloud": "https://soundcloud.com/search?q={q}",
    "js": "https://www.jiosaavn.com/search/{q}",
    "jiosaavn": "https://www.jiosaavn.com/search/{q}",
    "rd": "https://www.radio-browser.info/search?q={q}",
    "radio": "https://www.radio-browser.info/search?q={q}",
}
#: Playeon-style suffix shorthand: ``/play believer | sp``.
SITE_SUFFIX = re.compile(r"\|\s*([a-z]{2,10})\s*$", re.I)


def split_site(argument: str) -> tuple[str, str]:
    """Split ``"believer | sp"`` into ``("believer", "sp")``; site may be empty."""
    match = SITE_SUFFIX.search(argument or "")
    if not match:
        return (argument or "").strip(), ""
    site = match.group(1).lower()
    return (argument[: match.start()].strip(), site if site in SITES else "")


def site_search_url(site: str, query: str) -> str:
    """A deep link the user can open when the bot cannot resolve the site itself."""
    template = SITES.get(site.lower(), "")
    if not template:
        return ""
    from urllib.parse import quote_plus

    return template.format(q=quote_plus(query))


class PlaylistManager:
    """Playlist + chart storage on top of a plugin state store (durable)."""

    KEY = "playlists"

    def __init__(self, store: Any, *, limit: int = 40, chart_size: int = 30) -> None:
        self.store = store
        self.limit = limit
        self.chart_size = chart_size
        self.playlists: dict[str, Playlist] = {}
        self.plays: dict[str, int] = {}
        self._dirty = False

    async def load(self) -> "PlaylistManager":
        raw = await self.store.get(self.KEY, None) or {}
        if isinstance(raw, dict):
            self.playlists = {
                name: Playlist.from_dict(row) for name, row in (raw.get("playlists") or {}).items()
            }
            self.plays = {str(k): int(v) for k, v in (raw.get("plays") or {}).items()}
        return self

    async def save(self) -> None:
        if self.store is None:
            return
        await self.store.set(
            self.KEY,
            {
                "playlists": {name: pl.to_dict() for name, pl in self.playlists.items()},
                "plays": self.plays,
            },
        )
        self._dirty = False

    # -- playlists --------------------------------------------------------
    def names(self) -> list[str]:
        return sorted(self.playlists)

    def get(self, name: str) -> Playlist | None:
        return self.playlists.get((name or "").strip().lower())

    def put(self, playlist: Playlist) -> Playlist:
        name = playlist.name.strip().lower()[:40]
        playlist.name = name
        self.playlists[name] = playlist
        if len(self.playlists) > self.limit:
            oldest = min(self.playlists.values(), key=lambda pl: pl.created_at or 0)
            if oldest.name != name:
                self.playlists.pop(oldest.name, None)
        self._dirty = True
        return playlist

    def delete(self, name: str) -> bool:
        return self.playlists.pop((name or "").strip().lower(), None) is not None

    # -- charts -----------------------------------------------------------
    def record_play(self, track: Track) -> None:
        key = track.video_id or track.source or track.title
        if not key:
            return
        self.plays[key] = self.plays.get(key, 0) + 1
        self._dirty = True

    def chart_tracks(self, universe: list[Track]) -> list[Track]:
        """Most-played first, restricted to tracks we can still resolve."""
        by_key = {(t.video_id or t.source or t.title): t for t in universe}
        ranked = sorted(self.plays.items(), key=lambda item: item[1], reverse=True)
        out: list[Track] = []
        for key, plays in ranked:
            track = by_key.get(key)
            if track is None:
                continue
            track.extra["plays"] = plays
            out.append(track)
            if len(out) >= self.chart_size:
                break
        return out


def parse_range(argument: str, maximum: int) -> list[int] | None:
    """``"1-5,8"`` → ``[1,2,3,4,5,8]``; ``None`` when unparsable, ``[]`` when out of bounds."""
    if not (argument or "").strip():
        return None
    picks: list[int] = []
    for chunk in argument.replace(" ", ",").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            if not (start.isdigit() and end.isdigit()):
                return None
            low, high = int(start), int(end)
            if low > high:
                low, high = high, low
            picks.extend(range(low, min(high, maximum) + 1))
        elif chunk.isdigit():
            picks.append(int(chunk))
        else:
            return None
    return [index for index in dict.fromkeys(picks) if 1 <= index <= maximum]
