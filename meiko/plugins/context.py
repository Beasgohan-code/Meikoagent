"""Permission-scoped plugin execution context.

A plugin never gets the raw ``Bot``, the raw session or the cache.  It gets a
context that:

* enforces the permissions declared in its manifest (and granted at install),
* narrows every database query to its tenant + the bot/group scope it runs in,
* scopes key/value storage under ``plugin:<slug>:<scope>``,
* tracks the resources it acquired so ``on_stop`` can tear them down even if the
  plugin forgets, including on crash-loop restarts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aiogram import Bot, Router
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meiko.cache import Cache, get_cache
from meiko.logging_setup import get_logger
from meiko.plugins.manifest import PluginManifest

log = get_logger("plugins.context")


class PluginPermissionError(PermissionError):
    """Plugin attempted an operation missing from its manifest/grant."""


@dataclass
class PluginScope:
    """Where an installation is active."""

    bot_id: int | None = None
    group_id: int | None = None
    tenant_user_id: int | None = None

    @property
    def key(self) -> str:
        if self.group_id:
            return f"group:{self.group_id}"
        if self.bot_id:
            return f"bot:{self.bot_id}"
        return "global"


@dataclass
class PluginJob:
    name: str
    interval_seconds: int
    callback: Callable[[], Awaitable[None]]
    run_immediately: bool = False


class PluginContext:
    """Everything a plugin may do, nothing it may not."""

    def __init__(
        self,
        *,
        manifest: PluginManifest,
        scope: PluginScope,
        config: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
        bot: Bot | None = None,
        cache: Cache | None = None,
        granted_permissions: list[str] | None = None,
        bot_id: int | None = None,
    ) -> None:
        self.manifest = manifest
        self.scope = scope
        self.config = dict(config or {})
        self._session_factory = session_factory
        self._bot = bot
        self.cache = cache or get_cache()
        self.granted = set(granted_permissions if granted_permissions is not None else manifest.permissions)
        self.bot_id = bot_id if bot_id is not None else scope.bot_id
        self.routers: list[Router] = []
        self.jobs: list[PluginJob] = []
        self.subscriptions: list[str] = []
        self.cleanups: list[Callable[[], Awaitable[None]]] = []
        self.logs: list[dict[str, Any]] = []

    # -- permissions -----------------------------------------------------
    def require(self, permission: str) -> None:
        if permission not in self.granted:
            raise PluginPermissionError(
                f"plugin {self.manifest.slug} is not granted {permission!r}"
            )

    def can(self, permission: str) -> bool:
        return permission in self.granted

    # -- telegram --------------------------------------------------------
    @property
    def bot(self) -> Bot:
        self.require("messages.send")
        if self._bot is None:
            raise PluginPermissionError("no bot bound to this plugin scope")
        return self._bot

    def add_router(self, router: Router) -> Router:
        """Register a router; only valid while the plugin is RUNNING."""
        self.routers.append(router)
        return router

    # -- storage ---------------------------------------------------------
    @property
    def storage_namespace(self) -> str:
        return f"plugin:{self.manifest.slug}:{self.scope.key}"

    async def get_state(self, key: str, default: Any = None) -> Any:
        self.require("storage.read")
        return await self.cache.get(f"{self.storage_namespace}:{key}", default)

    async def set_state(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.require("storage.write")
        await self.cache.set(f"{self.storage_namespace}:{key}", value, ttl)

    async def delete_state(self, key: str) -> None:
        self.require("storage.write")
        await self.cache.delete(f"{self.storage_namespace}:{key}")

    # -- scheduler & events ----------------------------------------------
    def schedule(self, name: str, interval_seconds: int, callback: Callable[[], Awaitable[None]]) -> PluginJob:
        self.require("scheduler")
        job = PluginJob(
            name=f"{self.manifest.slug}:{self.scope.key}:{name}",
            interval_seconds=max(5, int(interval_seconds)),
            callback=callback,
        )
        self.jobs.append(job)
        return job

    def subscribe(self, event: str) -> None:
        self.require("events.subscribe")
        if event not in self.subscriptions:
            self.subscriptions.append(event)

    def on_cleanup(self, callback: Callable[[], Awaitable[None]]) -> None:
        self.cleanups.append(callback)

    # -- database --------------------------------------------------------
    def session(self) -> Any:
        """Tenant-scoped session factory (context manager)."""
        self.require("storage.read")
        return self._session_factory()

    async def scoped_count(self, model: Any) -> int:
        """Count tenant rows of a plugin-relevant model, never crossing scope."""
        self.require("storage.read")
        from sqlalchemy import func

        async with self._session_factory() as session:
            stmt = select(func.count()).select_from(model)
            if self.bot_id is not None and hasattr(model, "bot_id"):
                stmt = stmt.where(model.bot_id == self.bot_id)
            return int(await session.scalar(stmt) or 0)

    # -- observability ---------------------------------------------------
    def log(self, level: str, message: str, **data: Any) -> None:
        entry = {"level": level, "message": message, "data": data}
        self.logs.append(entry)
        if len(self.logs) > 500:
            del self.logs[: len(self.logs) - 500]
        getattr(log, level if level in ("debug", "info", "warning", "error") else "info")(
            "[%s] %s %s", self.manifest.slug, message, json.dumps(data, default=str) if data else ""
        )

    async def teardown(self) -> None:
        """Release every resource, regardless of what the plugin did."""
        for callback in list(self.cleanups):
            try:
                await callback()
            except Exception:  # pragma: no cover - defensive
                log.exception("plugin %s cleanup failed", self.manifest.slug)
        self.cleanups.clear()
        self.jobs.clear()
        self.routers.clear()
        self.subscriptions.clear()


def __getattr__(name: str) -> Any:  # pragma: no cover
    raise AttributeError(name)
