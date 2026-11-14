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
from typing import Any, Callable

DEFAULT_MODULES = ("music", "saver", "encoder", "filestore", "linkshare", "leech")


class ModuleStore:
    """Atomic JSON documents keyed by ``(module, scope)``.

Two persistence modes, chosen by the first constructor argument:

``ModuleStore("/data/modules")``
    One file per scope, written with ``mkstemp`` + ``os.replace`` so a crash can
    never leave a half-written document. This needs a filesystem that outlives
    the process.

``ModuleStore("supabase://<project-ref>/<bucket>", token=...)``
    Documents live in a Supabase Storage bucket over HTTPS. Nothing is kept on
    local disk, so a serverless deployment (Vercel) works instead of silently
    losing every setting on the next cold start. The interface is identical; the
    same ``_read``/``_write`` contract backs both. A ``warn`` hook fires when a
    write fails, because a module that keeps acknowledging saves while the
    backend is down is worse than one that says so.

The remote mode is deliberately thin: no caching layer beyond the existing
document cache, no retries beyond httpx's, and no invented bucket — an operator
creates it and grants the key access.
"""

    def __init__(
        self,
        root: str | Path,
        *,
        cache_documents: bool = True,
        token: str = "",
        url: str = "",
        timeout: float = 10.0,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        target = str(root)
        self.remote: _RemoteStore | None = None
        if target.startswith("supabase://"):
            self.remote = _RemoteStore.parse(target, token=token, url=url, timeout=timeout)
            self.root = Path(f"supabase://{self.remote.bucket}")
            self._warn = warn or (lambda message: None)
        else:
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)
            self._warn = warn or (lambda message: None)
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
        if self.remote is not None:
            document = self.remote.read(self._key(module, scope)) or {}
            if self._cache_documents:
                self._cache[key] = document
            return document
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
        if self.remote is not None:
            key = self._key(module, scope)
            if not self.remote.write(key, document):
                # never leave the cache claiming a save that did not happen
                self._cache.pop((module, scope), None)
                self._warn(f"could not persist {module}/{scope} to remote storage")
                return
            self._index_remote(module, scope)
            if self._cache_documents:
                self._cache[(module, scope)] = document
            return
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

    @staticmethod
    def _key(module: str, scope: str) -> str:
        safe_module = "".join(ch for ch in module if ch.isalnum() or ch in "-_") or "module"
        safe_scope = "".join(ch for ch in scope if ch.isalnum() or ch in "-_:.") or "global"
        return f"{safe_module}/{safe_scope.replace(':', '_')}.json"

    def _index_remote(self, module: str, scope: str) -> None:
        assert self.remote is not None
        index_key = f"{module}/_scopes.json"
        names = self.remote.read(index_key) or []
        names = [str(name) for name in names] if isinstance(names, list) else []
        if scope in names:
            return
        names.append(scope)
        self.remote.write(index_key, sorted(names))

    def scopes(self, module: str) -> list[str]:
        if self.remote is not None:
            names = self.remote.read(f"{module}/_scopes.json") or []
            return sorted(str(name) for name in names) if isinstance(names, list) else []
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
        if self.remote is not None:
            self._cache.pop((module, scope), None)
            removed = self.remote.delete(self._key(module, scope))
            names = self.remote.read(f"{module}/_scopes.json") or []
            if isinstance(names, list) and scope in names:
                self.remote.write(f"{module}/_scopes.json", [n for n in names if n != scope])
            return removed
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


class _RemoteStore:
    """Supabase Storage over the REST API (create/read/update/delete + list).

    Objects are addressed as ``<bucket>/<module>/<scope>.json``. The service key
    is required (row-level security is bypassed deliberately: this is a
    server-to-server call), and it is never logged or echoed in an error.
    """

    def __init__(
        self,
        *,
        base_url: str,
        bucket: str,
        token: str,
        timeout: float = 10.0,
        transport: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bucket = bucket
        self.token = token
        self.timeout = timeout
        self._transport = transport  # injectable for tests

    @classmethod
    def parse(cls, dsn: str, *, token: str = "", url: str = "", timeout: float = 10.0) -> "_RemoteStore":
        """``supabase://<project-ref>/<bucket>`` → settings; the key comes from ``token``."""
        rest = dsn[len("supabase://"):]
        project, _, bucket = rest.partition("/")
        if not project or not bucket:
            raise ValueError("supabase storage needs supabase://<project-ref>/<bucket>")
        base = url.rstrip("/") if url else f"https://{project}.supabase.co"
        return cls(base_url=f"{base}/storage/v1/object", bucket=bucket, token=token, timeout=timeout)

    def _client(self) -> Any:
        if self._transport is not None:
            return self._transport
        import httpx

        headers = {"apikey": self.token, "Authorization": f"Bearer {self.token}"}
        return httpx.Client(base_url=self.base_url, headers=headers, timeout=self.timeout)

    def _url(self, key: str) -> str:
        return f"/{self.bucket}/{key}"

    def read(self, key: str) -> Any:
        client = self._client()
        try:
            response = client.get(self._url(key))
        except Exception:  # network down: the caller degrades to an empty document
            return None
        if getattr(response, "status_code", 0) != 200:
            return None
        try:
            return response.json()
        except Exception:
            return None

    def write(self, key: str, document: Any) -> bool:
        client = self._client()
        payload = json.dumps(document, ensure_ascii=False, default=str).encode("utf-8")
        headers = {"Content-Type": "application/json", "x-upsert": "true"}
        try:
            response = client.post(self._url(key), content=payload, headers=headers)
            if getattr(response, "status_code", 500) >= 400:
                # the object may not exist yet: create it explicitly before failing
                created = client.post(
                    f"/{self.bucket}", content=payload, headers={**headers, "x-upsert": "true"}
                )
                return getattr(created, "status_code", 500) < 400
            return True
        except Exception:
            return False

    def delete(self, key: str) -> bool:
        client = self._client()
        try:
            response = client.delete(self._url(key))
        except Exception:
            return False
        return getattr(response, "status_code", 500) in {200, 204}


_STORE: ModuleStore | None = None


def get_module_store() -> ModuleStore:
    """Process-wide store rooted at the configured data directory."""
    global _STORE
    if _STORE is None:
        from meiko.config import get_settings

        settings = get_settings()
        dsn = str(getattr(settings, "module_store_url", "") or "").strip()
        if dsn:
            _STORE = ModuleStore(
                dsn,
                token=str(getattr(settings, "module_store_token", "") or ""),
                url=str(getattr(settings, "module_store_base_url", "") or ""),
            )
        else:
            root = Path(getattr(settings, "data_dir", "data")) / "modules"
            _STORE = ModuleStore(root)
    return _STORE


def reset_module_store() -> None:
    """Test hook: drop the cached store so a new data dir is picked up."""
    global _STORE
    _STORE = None


__all__ = ["DEFAULT_MODULES", "ModuleStore", "get_module_store", "reset_module_store"]
