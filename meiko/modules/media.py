"""Shared media service: metadata, MIME sniffing, hashing, temp workspaces.

Every module that receives a file (encoder, leech, filestore, saver, linkshare,
music) needs the same decisions: is this file allowed, what is it, what do we
call it on disk, where does the temporary copy live and when is it deleted.
Doing that once means one place to audit.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: Extensions we refuse outright regardless of the reported MIME type.
BLOCKED_EXTENSIONS = {
    ".exe", ".dll", ".so", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1",
    ".jar", ".apk", ".vbs", ".js", ".jse", ".wsf", ".hta", ".cpl",
}
#: Types worth flagging even when the extension looks harmless.
RISKY_MIME = {"application/x-dosexec", "application/x-msdownload", "application/x-sharedlib"}

AUDIO_MIME_PREFIX = "audio/"
VIDEO_MIME_PREFIX = "video/"
IMAGE_MIME_PREFIX = "image/"


class MediaError(ValueError):
    """The file violates a media policy (size, type, name)."""


@dataclass
class MediaInfo:
    name: str
    size: int
    mime: str
    extension: str
    kind: str  # audio | video | image | document
    sha256: str = ""
    width: int = 0
    height: int = 0
    duration: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def human_size(self) -> str:
        return human_size(self.size)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "MediaInfo | None":
        if not isinstance(data, dict) or "name" not in data:
            return None
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def human_size(value: int | float) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_duration(seconds: int | float | None) -> str:
    total = max(0, int(seconds or 0))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def safe_name(name: str, *, fallback: str = "file", max_length: int = 120) -> str:
    """Strip path traversal, control characters and reserved names."""
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*\0')
    base = base.strip(". ")
    if not base:
        base = fallback
    if len(base) > max_length:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: max_length - len(ext) - 1] + dot + ext) if dot and len(ext) <= 8 else base[:max_length]
    return base


def extension_of(name: str) -> str:
    return Path(safe_name(name)).suffix.lower()


def detect_kind(mime: str, extension: str = "") -> str:
    mime = (mime or "").lower()
    if mime.startswith(AUDIO_MIME_PREFIX):
        return "audio"
    if mime.startswith(VIDEO_MIME_PREFIX):
        return "video"
    if mime.startswith(IMAGE_MIME_PREFIX):
        return "image"
    if extension in {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac"}:
        return "audio"
    if extension in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"}:
        return "video"
    if extension in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "image"
    return "document"


def guess_mime(name: str, reported: str = "") -> str:
    if reported and reported != "application/octet-stream":
        return reported
    guessed, _encoding = mimetypes.guess_type(name)
    return guessed or reported or "application/octet-stream"


def sha256_of(path: str | Path, *, chunk: int = 1024 * 1024, limit_bytes: int = 512 * 1024 * 1024) -> str:
    """Hash a file, with a size ceiling so a huge upload cannot stall the bot."""
    digest = hashlib.sha256()
    read = 0
    with open(path, "rb") as handle:
        while chunk_bytes := handle.read(chunk):
            digest.update(chunk_bytes)
            read += len(chunk_bytes)
            if read >= limit_bytes:
                break
    return digest.hexdigest()


class TempWorkspace:
    """A per-job directory under ``root`` that is always cleaned up."""

    def __init__(self, root: str | Path, *, prefix: str = "job") -> None:
        self.root = Path(root)
        self.path = self.root / f"{prefix}-{uuid.uuid4().hex[:8]}"
        self.path.mkdir(parents=True, exist_ok=True)

    def file(self, name: str) -> Path:
        target = (self.path / safe_name(name)).resolve()
        if self.path.resolve() not in target.parents and target != self.path.resolve():
            raise MediaError("path escapes the job directory")
        return target

    def cleanup(self) -> int:
        removed = 0
        if not self.path.exists():
            return 0
        for entry in self.path.rglob("*"):
            if entry.is_file():
                removed += 1
        shutil.rmtree(self.path, ignore_errors=True)
        return removed

    def __enter__(self) -> "TempWorkspace":
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()


class MediaService:
    """Policy + metadata for anything the modules accept or produce."""

    def __init__(
        self,
        *,
        temp_root: str | Path = "data/tmp",
        max_bytes: int = 2000 * 1024 * 1024,
        ttl_seconds: int = 6 * 3600,
    ) -> None:
        self.temp_root = Path(temp_root)
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds

    def workspace(self, prefix: str = "job") -> TempWorkspace:
        return TempWorkspace(self.temp_root, prefix=prefix)

    def validate(self, name: str, size: int, mime: str = "") -> MediaInfo:
        """Raise :class:`MediaError` when a file must not be accepted."""
        safe = safe_name(name)
        extension = extension_of(safe)
        resolved_mime = guess_mime(safe, mime)
        if extension in BLOCKED_EXTENSIONS:
            raise MediaError(f"{extension} files are not accepted")
        if resolved_mime in RISKY_MIME:
            raise MediaError(f"{resolved_mime} files are not accepted")
        if size <= 0:
            raise MediaError("the file is empty")
        if size > self.max_bytes:
            raise MediaError(f"the file is {human_size(size)}; the limit is {human_size(self.max_bytes)}")
        return MediaInfo(
            name=safe,
            size=int(size),
            mime=resolved_mime,
            extension=extension,
            kind=detect_kind(resolved_mime, extension),
        )

    def inspect(self, path: str | Path, *, name: str = "", hash_file: bool = False) -> MediaInfo:
        target = Path(path)
        info = self.validate(name or target.name, target.stat().st_size)
        if hash_file:
            info.sha256 = sha256_of(target)
        return info

    def sweep(self, *, now: float | None = None) -> int:
        """Delete temp directories older than the TTL; returns files removed."""
        cutoff = (now or time.time()) - self.ttl_seconds
        removed = 0
        for entry in self.temp_root.iterdir():
            if not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime >= cutoff:
                    continue
            except OSError:  # pragma: no cover
                continue
            removed += sum(1 for child in entry.rglob("*") if child.is_file())
            shutil.rmtree(entry, ignore_errors=True)
        return removed

    def usage(self) -> dict[str, Any]:
        files = 0
        total = 0
        for entry in self.temp_root.rglob("*"):
            if entry.is_file():
                files += 1
                total += entry.stat().st_size
        return {"files": files, "bytes": total, "human": human_size(total), "root": str(self.temp_root)}


def get_media_service() -> MediaService:
    """Process-wide media service rooted at the configured data directory."""
    from meiko.modules.store import get_module_store

    store = get_module_store()
    return MediaService(temp_root=store.root.parent / "tmp")


__all__ = [
    "BLOCKED_EXTENSIONS",
    "MediaError",
    "MediaInfo",
    "MediaService",
    "RISKY_MIME",
    "TempWorkspace",
    "detect_kind",
    "extension_of",
    "get_media_service",
    "guess_mime",
    "human_duration",
    "human_size",
    "safe_name",
    "sha256_of",
]
