"""Plugin SDK: the base class every Meiko plugin implements.

Lifecycle contract (mirrors the database states)::

    AVAILABLE -> INSTALLED -> ENABLED -> CONFIGURED -> RUNNING
                                   ^            |
                                   +--- DISABLED+

* ``on_install``   – once per installation (seed storage, validate config).
* ``on_enable``    – configuration accepted; may refuse by raising.
* ``on_start``     – only called for RUNNING plugins; register handlers/commands/
                     scheduled jobs *through the context*, never globally.
* ``on_stop``      – must release everything ``on_start`` acquired.
* ``on_disable``   – after stop; cleanup that survives re-enabling.

Plugins never receive a raw ``Bot`` or an unrestricted session: they get a
:class:`~meiko.plugins.context.PluginContext` whose abilities are limited to the
permissions declared in their manifest.
"""

from __future__ import annotations

from typing import Any, ClassVar

from aiogram import Router

from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest


class PluginError(RuntimeError):
    """Raised when a plugin misbehaves during its lifecycle."""


class MeikoPlugin:
    """Base class for Meiko plugins."""

    manifest: ClassVar[PluginManifest]

    def __init__(self) -> None:
        self.context: PluginContext | None = None
        self.router: Router | None = None
        self.running: bool = False

    # -- lifecycle hooks -------------------------------------------------
    async def on_install(self, ctx: PluginContext) -> None:
        """Seed defaults / migrate plugin storage."""

    async def on_enable(self, ctx: PluginContext) -> None:
        """Validate configuration before the plugin is allowed to run."""

    async def on_start(self, ctx: PluginContext) -> None:
        """Register routers, commands, jobs and event subscriptions."""

    async def on_stop(self, ctx: PluginContext) -> None:
        """Undo everything done in ``on_start``."""

    async def on_disable(self, ctx: PluginContext) -> None:
        """Cleanup that should survive a later re-enable."""

    async def on_config_change(self, ctx: PluginContext, previous: dict[str, Any]) -> None:
        """React to a configuration update while running."""

    # -- helpers ---------------------------------------------------------
    def setup(self, ctx: PluginContext) -> Router:
        """Called by the manager with a permission-scoped context."""
        if self.manifest is None:
            raise PluginError(f"{type(self).__name__} has no manifest")
        self.context = ctx
        self.router = Router(name=f"plugin:{self.manifest.slug}")
        return self.router

    def config(self, key: str, default: Any = None) -> Any:
        if self.context is None:
            return default
        return self.context.config.get(key, default)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        slug = getattr(self.manifest, "slug", type(self).__name__)
        return f"<{type(self).__name__} slug={slug} running={self.running}>"


class PluginRegistry:
    """In-process registry of implemented plugin classes (builtin + approved)."""

    def __init__(self) -> None:
        self._classes: dict[str, type[MeikoPlugin]] = {}

    def register(self, plugin_cls: type[MeikoPlugin]) -> type[MeikoPlugin]:
        manifest = getattr(plugin_cls, "manifest", None)
        if manifest is None:
            raise PluginError(f"{plugin_cls.__name__} does not declare a manifest")
        manifest.validate()
        self._classes[manifest.slug] = plugin_cls
        return plugin_cls

    def get(self, slug: str) -> type[MeikoPlugin] | None:
        return self._classes.get(slug)

    def create(self, slug: str) -> MeikoPlugin:
        plugin_cls = self._classes.get(slug)
        if plugin_cls is None:
            raise PluginError(
                f"plugin {slug!r} has no implementation – install an approved package first"
            )
        return plugin_cls()

    def manifests(self) -> list[PluginManifest]:
        return [cls.manifest for cls in self._classes.values()]

    def slugs(self) -> list[str]:
        return sorted(self._classes)

    def clear(self) -> None:
        self._classes.clear()


registry = PluginRegistry()


def plugin(cls: type[MeikoPlugin]) -> type[MeikoPlugin]:
    """Class decorator registering a plugin implementation."""
    return registry.register(cls)
