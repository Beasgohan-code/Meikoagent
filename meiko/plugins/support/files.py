"""Quota-aware, traversal-safe workspace for plugin file operations.

Every plugin gets its own directory under ``DATA_DIR/plugins/<slug>/`` and every
write goes through this module, so a plugin can never:

* escape its directory (``..``, absolute paths, symlinked parents),
* exceed the configured quota for its scope,
* leave unbounded files behind (TTL sweep + explicit cleanup).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from meiko.config import get_settings

_UNSAFE = re.compile(r"[^\w\-. ()\[\]&+#@% ]+", re.UNICODE)
_DOTS = re.compile(r"\.{2,}")
MAX_NAME_LENGTH = 120


class WorkspaceError(RuntimeError):
    """Base error for workspace operations."""


class FileTooLarge(WorkspaceError):
    """Upload exceeds the per-file limit."""


class QuotaExceeded(WorkspaceError):
    """Scope would exceed its storage quota."""

    def __init__(self, used: int, quota: int, needed: int = 0) -> None:
        super().__init__(
            f"quota exceeded: {human_size(used)} of {human_size(quota)} used"
            + (f", {human_size(needed)} needed" if needed else "")
        )
        self.used = used
        self.quota = quota
        self.needed = needed


def human_size(size: float | int | None) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def sanitize_ext(name: str) -> str:
    suffix = Path(name or "").suffix.lower()
    if len(suffix) > 10 or not re.fullmatch(r"\.[a-z0-9]{1,9}", suffix):
        return ""
    return suffix


def safe_filename(name: str, *, fallback: str = "file", max_length: int = MAX_NAME_LENGTH) -> str:
    """Return a filesystem-safe file name that keeps the extension."""
    raw = (name or "").replace("\\", "/").split("/")[-1].strip()
    ext = sanitize_ext(raw)
    stem = _UNSAFE.sub("_", raw[: len(raw) - len(ext)] if ext else raw)
    stem = _DOTS.sub(".", stem).strip(" ._") or fallback
    limit = max(8, max_length - len(ext))
    stem = stem[:limit].rstrip(" ._") or fallback
    return f"{stem}{ext}"


@dataclass(frozen=True)
class StoredObject:
    path: Path
    name: str
    size: int
    sha256: str


class Workspace:
    """Directory scoped to one plugin (and optionally one bot/user)."""

    def __init__(
        self,
        slug: str,
        *,
        scope: str = "global",
        quota_bytes: int | None = None,
        max_file_bytes: int | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.slug = re.sub(r"[^a-z0-9_]", "_", slug.lower())
        self.scope = re.sub(r"[^a-zA-Z0-9_:-]", "_", scope or "global")
        root = Path(settings.data_dir) / "plugins" / self.slug
        self.root = (root / self.scope).resolve()
        self.quota_bytes = quota_bytes if quota_bytes is not None else 1024 * 1024 * 512
        self.max_file_bytes = max_file_bytes if max_file_bytes is not None else 1024 * 1024 * 45
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else 60 * 60 * 24 * 3

    # -- paths -----------------------------------------------------------
    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def path_for(self, name: str) -> Path:
        """Resolve a child path, refusing anything outside the workspace."""
        candidate = (self.root / safe_filename(name)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError("path escapes the plugin workspace")
        return candidate

    # -- accounting ------------------------------------------------------
    def usage(self) -> int:
        if not self.root.exists():
            return 0
        total = 0
        for entry in self.root.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        return total

    def entries(self) -> list[StoredObject]:
        if not self.root.exists():
            return []
        out = []
        for entry in sorted(self.root.rglob("*")):
            if entry.is_file() and not entry.is_symlink():
                out.append(
                    StoredObject(
                        path=entry,
                        name=entry.name,
                        size=entry.stat().st_size,
                        sha256="",
                    )
                )
        return out

    def check_capacity(self, incoming: int) -> None:
        if incoming > self.max_file_bytes:
            raise FileTooLarge(
                f"file is {human_size(incoming)}, limit is {human_size(self.max_file_bytes)}"
            )
        used = self.usage()
        if used + incoming > self.quota_bytes:
            raise QuotaExceeded(used, self.quota_bytes, incoming)

    # -- writes ----------------------------------------------------------
    def write_bytes(self, name: str, data: bytes) -> StoredObject:
        self.check_capacity(len(data))
        target = self.path_for(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return StoredObject(
            path=target, name=target.name, size=len(data), sha256=hashlib.sha256(data).hexdigest()
        )

    def write_stream(self, name: str, chunks: Iterable[bytes]) -> StoredObject:
        target = self.path_for(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with target.open("wb") as handle:
            for chunk in chunks:
                size += len(chunk)
                if size > self.max_file_bytes:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise FileTooLarge(
                        f"file exceeds {human_size(self.max_file_bytes)} before completion"
                    )
                digest.update(chunk)
                handle.write(chunk)
        self.check_capacity(0)
        return StoredObject(path=target, name=target.name, size=size, sha256=digest.hexdigest())

    def open_for_read(self, name: str) -> Path:
        target = self.path_for(name)
        if not target.exists():
            raise FileNotFoundError(name)
        return target

    # -- deletes & sweeps ------------------------------------------------
    def remove(self, name: str) -> bool:
        target = self.path_for(name)
        if target.is_file():
            target.unlink()
            return True
        return False

    def purge(self) -> int:
        removed = 0
        for entry in self.entries():
            entry.path.unlink(missing_ok=True)
            removed += 1
        if self.root.exists() and not any(self.root.iterdir()):
            shutil.rmtree(self.root, ignore_errors=True)
        return removed

    def sweep(self, ttl_seconds: int | None = None) -> int:
        """Delete objects older than the TTL; returns how many were removed."""
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        cutoff = time.time() - ttl
        removed = 0
        for entry in self.entries():
            try:
                if entry.path.stat().st_mtime < cutoff:
                    entry.path.unlink()
                    removed += 1
            except OSError:  # pragma: no cover - race with concurrent cleanup
                continue
        return removed

    def stats(self) -> dict[str, object]:
        entries = self.entries()
        used = sum(entry.size for entry in entries)
        return {
            "slug": self.slug,
            "scope": self.scope,
            "files": len(entries),
            "used_bytes": used,
            "quota_bytes": self.quota_bytes,
            "max_file_bytes": self.max_file_bytes,
            "path": str(self.root),
        }
