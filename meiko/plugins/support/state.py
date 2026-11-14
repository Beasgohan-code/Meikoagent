"""Durable plugin storage.

``PluginContext.get_state/set_state`` go through the cache, which is in-memory
when ``REDIS_URL`` is unset.  That is fine for counters and rate limits, but not
for a plugin's own bookkeeping (the saver's dedupe index, the scheduled-post
queue, the heartbeat history): a restart would silently lose them.

``PluginState`` mirrors every write to ``DATA_DIR/plugins/<slug>/<scope>/_state/``
(after the write returns, so the hot path does not pay for the fsync), and falls
back to that mirror when the cache misses.  No extra table, no migration, and it
keeps working when Redis *is* configured — cache first, disk second.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from meiko.config import get_settings

MIRROR_DIRNAME = "_state"
_write_lock = asyncio.Lock()


def state_dir(slug: str, scope: str) -> Path:
    base = Path(get_settings().data_dir) / "plugins" / slug / scope.replace(":", "_")
    path = base / MIRROR_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(slug: str, scope: str, key: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)[:80] or "state"
    return state_dir(slug, scope) / f"{safe}.json"


def write_mirror(slug: str, scope: str, key: str, value: Any) -> Path:
    """Atomically persist ``value`` (temp file + rename)."""
    target = state_path(slug, scope, key)
    payload = json.dumps(value, default=str)
    handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):  # pragma: no cover - rename already moved it
            os.unlink(temp_name)
    return target


def read_mirror(slug: str, scope: str, key: str, default: Any = None) -> Any:
    path = state_path(slug, scope, key)
    if not path.is_file():
        return default
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):  # pragma: no cover - corrupt file is not fatal
        return default


def drop_mirror(slug: str, scope: str, key: str) -> bool:
    path = state_path(slug, scope, key)
    if path.is_file():
        path.unlink(missing_ok=True)
        return True
    return False


class PluginState:
    """Cache-first, disk-backed plugin state for one scope."""

    def __init__(self, context: Any) -> None:
        self._context = context
        self.slug = context.manifest.slug
        self.scope = context.scope.key

    async def get(self, key: str, default: Any = None) -> Any:
        value = await self._context.get_state(key)
        if value is not None:
            return value
        return read_mirror(self.slug, self.scope, key, default)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self._context.set_state(key, value, ttl)
        async with _write_lock:  # small, sequential writes only
            await asyncio.to_thread(write_mirror, self.slug, self.scope, key, value)

    async def delete(self, key: str) -> None:
        await self._context.delete_state(key)
        async with _write_lock:
            await asyncio.to_thread(drop_mirror, self.slug, self.scope, key)

    @property
    def root(self) -> Path:
        return state_dir(self.slug, self.scope)


__all__ = [
    "MIRROR_DIRNAME",
    "PluginState",
    "drop_mirror",
    "read_mirror",
    "state_dir",
    "state_path",
    "write_mirror",
]
