"""Vault index and quota accounting.

The bytes live in the plugin :class:`~meiko.plugins.support.files.Workspace`; the
*index* (names, owners, sizes, hashes, source metadata) lives in plugin storage
under the installation scope, so two groups, or two bots, never see each
other's index even if they share a host.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from meiko.plugins.support import Workspace, human_size


class StoreError(RuntimeError):
    """Vault operation refused."""


@dataclass
class StoredFile:
    name: str
    size: int
    sha256: str
    owner_id: int
    uploaded_at: float
    source: str = "upload"        # upload | saved_post | music | generated
    media_type: str = "document"
    caption: str = ""
    tags: list[str] = field(default_factory=list)
    file_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoredFile":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VaultIndex:
    """Index kept in plugin storage; all mutations are copy-on-write."""

    KEY = "index:files"

    def __init__(self, context: Any, workspace: Workspace) -> None:
        self.context = context
        self.workspace = workspace

    async def load(self) -> list[StoredFile]:
        raw = await self.context.get_state(self.KEY, []) or []
        files: list[StoredFile] = []
        for entry in raw:
            if isinstance(entry, dict):
                files.append(StoredFile.from_dict(entry))
        return files

    async def save(self, files: Iterable[StoredFile]) -> None:
        await self.context.set_state(self.KEY, [item.to_dict() for item in files])

    async def add(self, item: StoredFile) -> StoredFile:
        files = await self.load()
        if any(existing.name == item.name for existing in files):
            raise StoreError(f"a file named {item.name!r} already exists – delete it first")
        files.append(item)
        await self.save(files)
        return item

    async def remove(self, name: str) -> StoredFile | None:
        files = await self.load()
        kept, removed = [], None
        for item in files:
            if item.name == name and removed is None:
                removed = item
                continue
            kept.append(item)
        if removed is None:
            return None
        await self.save(kept)
        self.workspace.remove(name)
        return removed

    async def usage_for(self, owner_id: int) -> int:
        return sum(item.size for item in await self.load() if item.owner_id == owner_id)

    async def search(self, query: str = "", owner_id: int | None = None) -> list[StoredFile]:
        needle = (query or "").lower().strip()
        files = await self.load()
        if owner_id is not None:
            files = [item for item in files if item.owner_id == owner_id]
        if needle:
            files = [
                item
                for item in files
                if needle in item.name.lower()
                or needle in item.caption.lower()
                or any(needle in tag.lower() for tag in item.tags)
            ]
        return sorted(files, key=lambda item: item.uploaded_at, reverse=True)

    async def total(self) -> tuple[int, int]:
        files = await self.load()
        return len(files), sum(item.size for item in files)

    async def sweep(self, ttl_days: int) -> int:
        cutoff = time.time() - max(1, ttl_days) * 86400
        files = await self.load()
        kept, expired = [], []
        for item in files:
            (expired if item.uploaded_at < cutoff else kept).append(item)
        if expired:
            for item in expired:
                self.workspace.remove(item.name)
            await self.save(kept)
        return len(expired)


def describe_usage(workspace: Workspace, index: VaultIndex, quota_mb: int) -> str:
    """Human summary used by /storestats (pure function → easy to test)."""
    return (
        f"{workspace.stats()['files']} files · quota {quota_mb} MB · "
        f"workspace {human_size(workspace.usage())}"
    )


__all__ = ["StoreError", "StoredFile", "VaultIndex", "describe_usage"]
