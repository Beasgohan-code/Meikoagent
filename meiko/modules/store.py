"""Durable per-module state, shared by the API and the bot runtime.

The plugins already have ``PluginState`` (cache-first, disk-backed) but the API
process has no plugin context, and the webapp must read the same data the bot
writes.  ``ModuleStore`` is that common surface: JSON documents under
``DATA_DIR/modules/<module>/<scope>.json``, written atomically, with per-scope
namespaces so one bot can never read another bot's data.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_MODULES = ("music", "saver", "encoder", "filestore", "linkshare", "leech")


class ModuleStore:
    """Atomic JSON documents keyed by ``(module, scope)``."""

    def __init__(self, root: str | Path, *, cache_documents: bool = True) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._cache_documents = cache_documents
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    # -- paths -------------------------------------------------------------
    def _path(self, module: str, scope: str) -> Path:
        safe_module = "".join(ch for ch in module if ch.isalnum() or ch in "-_") or "module"
        safe_scope = "".join(ch for ch in scope if ch.isalnum() or ch in "-_:.") or "global"
        target = self.root / safe_module
        target.mkdir(parents=True, exist_ok=True)
        return target / f"{safe_scope.replace(':', '_')}.json"

    def lock(self, module: str, scope: str) -> asyncio.Lock:
        return self._locks.setdefault((module, scope), asyncio.Lock())

    # -- io ----------------------------------------------------------------
    def read(self, module: str, scope: str = "global") -> dict[str, Any]:
        key = (module, scope)
        if self._cache_documents and key in self._cache:
            return self._cache[key]
        path = self._path(module, scope)
        document: dict[str, Any] = {}
        if path.is_file():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # a corrupt document must never take the bot down: keep a copy
                try:
                    path.replace(path.with_suffix(".corrupt"))
                except OSError:
                    pass
                document = {}
        if not isinstance(document, dict):
            document = {}
        if self._cache_documents:
            self._cache[key] = document
        return document

    def write(self, module: str, scope: str, document: dict[str, Any]) -> None:
        path = self._path(module, scope)
        self._index(module, scope)
        payload = json.dumps(document, ensure_ascii=False, indent=1, default=str)
        handle, temp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        if self._cache_documents:
            self._cache[(module, scope)] = document

    def _index(self, module: str, scope: str) -> None:
        """Record scope names so ``scopes()`` can report them exactly."""
        index = self.root / module / "_scopes.json"
        names: list[str] = []
        if index.is_file():
            try:
                loaded = json.loads(index.read_text(encoding="utf-8"))
                names = [str(name) for name in loaded] if isinstance(loaded, list) else []
            except (json.JSONDecodeError, OSError):
                names = []
        if scope in names:
            return
        names.append(scope)
        handle, temp = tempfile.mkstemp(dir=str(index.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(sorted(names)))
            os.replace(temp, index)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def update(self, module: str, scope: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge a patch into the document (top-level keys) and persist."""
        document = dict(self.read(module, scope))
        document.update(patch)
        self.write(module, scope, document)
        return document

    def scopes(self, module: str) -> list[str]:
        """Every scope this module has persisted.

        Filenames replace ``:`` with ``_``, which is not reversible, so an index
        document records the real scope names.  Without one (legacy data) the
        stems are returned rather than pretending to know better.
        """
        folder = self.root / module
        if not folder.is_dir():
            return []
        index = folder / "_scopes.json"
        if index.is_file():
            try:
                names = json.loads(index.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                names = []
            if isinstance(names, list):
                return sorted(str(name) for name in names)
        return sorted(path.stem for path in folder.glob("*.json") if path.stem != "_scopes")

    def invalidate(self, module: str | None = None) -> None:
        """Drop cached reads so the next call re-reads from disk."""
        if module is None:
            self._cache.clear()
        else:
            for key in [k for k in self._cache if k[0] == module]:
                self._cache.pop(key, None)

    def forget(self, module: str, scope: str) -> bool:
        """Delete one scope's document and its index entry."""
        path = self._path(module, scope)
        self._cache.pop((module, scope), None)
        removed = False
        if path.is_file():
            path.unlink()
            removed = True
        index = self.root / module / "_scopes.json"
        if index.is_file():
            try:
                names = json.loads(index.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                names = []
            if isinstance(names, list) and scope in names:
                names = [name for name in names if name != scope]
                index.write_text(json.dumps(sorted(names)), encoding="utf-8")
        return removed


_STORE: ModuleStore | None = None


def get_module_store() -> ModuleStore:
    """Process-wide store rooted at the configured data directory."""
    global _STORE
    if _STORE is None:
        from meiko.config import get_settings

        settings = get_settings()
        root = Path(getattr(settings, "data_dir", "data")) / "modules"
        _STORE = ModuleStore(root)
    return _STORE


def reset_module_store() -> None:
    """Test hook: drop the cached store so a new data dir is picked up."""
    global _STORE
    _STORE = None


__all__ = ["DEFAULT_MODULES", "ModuleStore", "get_module_store", "reset_module_store"]
