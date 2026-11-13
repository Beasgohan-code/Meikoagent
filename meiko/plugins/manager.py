"""Plugin lifecycle manager.

Real lifecycle, not "register everything at boot":

    AVAILABLE -> INSTALLED -> ENABLED -> CONFIGURED -> RUNNING

A plugin that is installed but disabled (or enabled but not configured, or not
running) registers **zero** handlers, commands, schedulers and event
subscriptions.  Every registration happens inside ``start()`` and is undone in
``stop()``; :class:`RunningPlugin` keeps that bookkeeping so nothing can leak.

Activation scopes
-----------------
* ``global`` – available platform-wide, still disable-able per bot,
* ``bot``    – enabled for one bot,
* ``group``  – enabled for a single group of a bot, overriding the bot scope.

Resolution order for "is plugin X active for bot B in group G?":
``group`` -> ``bot`` -> ``global``; the first explicit state wins.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Iterable

from aiogram import Bot, Dispatcher, Router

from meiko.cache import Cache, get_cache
from meiko.core.commands import CommandRegistry, registry as command_registry
from meiko.config import get_settings
from meiko.db.base import get_sessionmaker
from meiko.db.models import Plugin, PluginInstallation, PluginLog
from meiko.logging_setup import get_logger
from meiko.plugins.base import MeikoPlugin, PluginError, PluginRegistry, registry as plugin_registry
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.context import PluginContext, PluginJob, PluginScope

log = get_logger("plugins.manager")

ACTIVE_STATES = {"running"}
ENABLED_STATES = {"enabled", "configured", "running"}

#: The vocabulary the API and the console use for "state of this plugin".
#: ``running``/``stopped`` come from the runtime, the rest from the database row
#: plus the approval and permission checks – never guessed from a timestamp.
RUNTIME_STATUSES = (
    "available",
    "installed",
    "approved",
    "enabled",
    "configured",
    "running",
    "stopped",
    "failed",
    "blocked",
    "update_available",
    "missing_dependency",
    "missing_permission",
    "missing_configuration",
)


def missing_permissions(plugin: Any) -> list[str]:
    """Manifest permissions that are not part of the platform permission model.

    A plugin cannot self-grant a permission at runtime, so a manifest that asks
    for something the platform does not define can never run – that is a
    ``blocked`` plugin, not a broken one.
    """
    from meiko.plugins.manifest import PERMISSIONS

    declared = list(getattr(plugin, "permissions", None) or [])
    return [permission for permission in declared if permission not in PERMISSIONS]


def permission_changes(plugin: Any) -> list[str]:
    """Permissions a newer manifest wants that the approved record never granted."""
    manifest = dict(getattr(plugin, "manifest", None) or {})
    approved = set(manifest.get("permissions") or [])
    current = set(getattr(plugin, "permissions", None) or [])
    return sorted(current - approved)


def runtime_status(
    *,
    plugin: Any,
    record: PluginInstallation | None,
    live: bool,
    bot_running: bool,
    required_config: list[str] | None = None,
    update_available: bool = False,
) -> str:
    """One authoritative status string for a (plugin, bot) pair.

    Order matters – the first condition that applies is the honest answer:

    * not installed anywhere -> ``available``
    * the platform blocks it, or it asks for permissions it does not have ->
      ``blocked`` / ``missing_permission``
    * an update is pending -> ``update_available``
    * the runtime says it is running -> ``running``
    * it is meant to run but the manager has no instance ->
      ``failed`` (bot running) or ``stopped`` (bot down)
    * still missing required configuration -> ``missing_configuration``
    """
    if record is None:
        return "available"
    if str(getattr(plugin, "status", "approved")) in ("blocked", "pending", "rejected"):
        return "blocked"
    if missing_permissions(plugin):
        return "missing_permission"
    if update_available:
        return "update_available"
    if live:
        return "running"
    if record.state == "available":
        return "available"
    if record.state == "installed":
        return "installed"
    if record.state == "disabled":
        return "stopped"
    if required_config:
        return "missing_configuration"
    if record.state in ENABLED_STATES:
        return "failed" if bot_running else "stopped"
    return record.state


class PluginLifecycleError(PluginError):
    """Lifecycle transition refused."""


class PluginPermissionError(PluginLifecycleError):
    """The plugin asks for a capability its owner has not granted."""


@dataclass
class RunningPlugin:
    """Bookkeeping for one running plugin instance in one scope."""

    slug: str
    scope: PluginScope
    instance: MeikoPlugin
    context: PluginContext
    router: Router | None = None
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    started_at: float = 0.0
    commands: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "scope": self.scope.key,
            "bot_id": self.scope.bot_id,
            "group_id": self.scope.group_id,
            "commands": list(self.commands),
            "jobs": [job.name for job in self.context.jobs],
            "events": list(self.context.subscriptions),
            "tasks": len([t for t in self.tasks if not t.done()]),
        }


class PluginManager:
    """Runtime plugin supervisor shared by the main bot and clone workers."""

    def __init__(
        self,
        *,
        plugins: PluginRegistry | None = None,
        commands: CommandRegistry | None = None,
        cache: Cache | None = None,
    ) -> None:
        self.plugins = plugins or plugin_registry
        # NB: never use `commands or ...` – an *empty* CommandRegistry is falsy
        # (it defines __len__), which would silently swap in the global registry.
        self.commands = commands if commands is not None else command_registry
        self.cache = cache or get_cache()
        self._running: dict[str, RunningPlugin] = {}
        self._lock = asyncio.Lock()
        self.persist_logs = not get_settings().is_testing

    # -- introspection ---------------------------------------------------
    @staticmethod
    def key(slug: str, scope: PluginScope) -> str:
        return f"{slug}|{scope.key}"

    def is_running(self, slug: str, scope: PluginScope) -> bool:
        return self.key(slug, scope) in self._running

    def running_summary(self) -> list[dict[str, Any]]:
        return [rp.summary() for rp in self._running.values()]

    def active_commands(self) -> list[str]:
        out: list[str] = []
        for rp in self._running.values():
            out.extend(rp.commands)
        return sorted(set(out))

    # -- scope resolution ------------------------------------------------
    @staticmethod
    def resolve_scope(
        installs: Iterable[PluginInstallation],
        slug: str,
        bot_id: int | None,
        group_id: int | None,
        plugin_ids: "dict[str, int] | None" = None,
    ) -> PluginInstallation | None:
        """Pick the most specific installation of ``slug`` for (bot, group).

        Precedence is ``group -> bot -> global`` and the *first explicit state
        wins*: a ``disabled`` group row overrides an ``enabled`` bot row, which is
        what "disable it in this one chat" means.

        ``plugin_ids`` maps slug -> plugin id.  Without it, rows cannot be told
        apart: two plugins installed on the same bot are two rows with the same
        ``bot_id``, and the caller would get whichever row came last.  Every
        caller inside the platform passes the map; the parameter stays optional so
        a single-plugin caller (and the tests) can omit it.
        """
        wanted_plugin_id = (plugin_ids or {}).get(slug)
        candidates = [
            install
            for install in installs
            if install.plugin_id is not None
            and (wanted_plugin_id is None or install.plugin_id == wanted_plugin_id)
        ]
        by_scope: dict[str, PluginInstallation] = {}
        for install in candidates:
            if install.group_id is not None:
                if install.group_id == group_id:
                    by_scope["group"] = install
            elif install.bot_id is not None:
                if install.bot_id == bot_id:
                    by_scope["bot"] = install
            else:
                by_scope["global"] = install
        for scope in ("group", "bot", "global"):
            install = by_scope.get(scope)
            if install is not None:
                return install
        return None

    # -- lifecycle -------------------------------------------------------
    async def start(
        self,
        slug: str,
        *,
        installation: PluginInstallation,
        scope: PluginScope,
        bot: Bot | None = None,
        dispatcher: Dispatcher | None = None,
        config: dict[str, Any] | None = None,
        granted_permissions: list[str] | None = None,
    ) -> RunningPlugin:
        """Move an installation to RUNNING and register its resources."""
        async with self._lock:
            key = self.key(slug, scope)
            if key in self._running:
                return self._running[key]
            if installation.state not in ("enabled", "configured"):
                raise PluginLifecycleError(
                    f"plugin {slug} is {installation.state}; enable and configure it before starting"
                )
            if installation.state == "enabled":
                installation.state = "configured"

            plugin_cls = self.plugins.get(slug)
            if plugin_cls is None:
                raise PluginLifecycleError(
                    f"plugin {slug!r} has no approved implementation; refusing to execute code"
                )
            manifest = plugin_cls.manifest
            if granted_permissions is not None:
                from meiko.plugins.manifest import PERMISSIONS as _PLATFORM_PERMISSIONS

                declared = [
                    p for p in (manifest.permissions or []) if p in _PLATFORM_PERMISSIONS
                ]
                ungranted = [p for p in declared if p not in granted_permissions]
                if ungranted:
                    # Declared is not granted.  Refusing to start is the honest
                    # answer; running with a silently reduced permission set would
                    # let the plugin fail half-way through an operation instead.
                    raise PluginPermissionError(
                        f"plugin {slug} requires permissions the owner has not granted: "
                        + ", ".join(ungranted)
                    )
            missing = [d for d in manifest.dependencies if d not in
                       {rp.slug for rp in self._running.values()}]
            if missing:
                raise PluginLifecycleError(
                    f"plugin {slug} requires running dependencies: {', '.join(missing)}"
                )

            instance = self.plugins.create(slug)
            context = PluginContext(
                manifest=manifest,
                scope=scope,
                config=config or dict(installation.config or {}),
                session_factory=get_sessionmaker(),
                bot=bot,
                cache=self.cache,
                granted_permissions=granted_permissions,
            )
            router = instance.setup(context)
            await instance.on_start(context)
            instance.running = True

            if dispatcher is not None:
                dispatcher.include_router(context.routers[0] if context.routers else router)

            # The plugin handlers are attached to the dispatcher above, so the
            # declared commands must become visible to the command registry too –
            # otherwise Telegram's menu, the Command Studio and /help would never
            # mention a plugin command.  Unregistering happens on stop.
            self._register_commands(manifest)

            rp = RunningPlugin(
                slug=slug,
                scope=scope,
                instance=instance,
                context=context,
                router=router,
                commands=[c.name for c in self.commands.by_plugin(slug)],
                started_at=asyncio.get_event_loop().time(),
            )
            installation.state = "running"
            self._running[key] = rp
            self._start_jobs(rp)
            for event in context.subscriptions:
                log.info("plugin %s subscribed to %s", slug, event)
            await self._write_log(installation, "info", f"plugin started in scope {scope.key}")
            log.info("plugin %s running in %s (%d commands)", slug, scope.key, len(rp.commands))
            return rp

    def _start_jobs(self, rp: RunningPlugin) -> None:
        for job in list(rp.context.jobs):
            task = asyncio.create_task(self._job_loop(rp, job), name=f"plugin-job:{job.name}")
            rp.tasks.append(task)

    async def _job_loop(self, rp: RunningPlugin, job: PluginJob) -> None:
        try:
            if job.run_immediately:
                try:
                    await job.callback()
                except asyncio.CancelledError:
                    raise
                except Exception:  # pragma: no cover - plugin bug isolation
                    log.exception("plugin job %s failed on first run", job.name)
            while True:
                await asyncio.sleep(job.interval_seconds)
                try:
                    await job.callback()
                except asyncio.CancelledError:
                    raise
                except Exception:  # pragma: no cover - plugin bug isolation
                    log.exception("plugin job %s failed", job.name)
        except asyncio.CancelledError:
            log.debug("plugin job %s cancelled", job.name)
            raise

    async def stop(
        self,
        slug: str,
        scope: PluginScope,
        *,
        installation: PluginInstallation | None = None,
        dispatcher: Dispatcher | None = None,
        new_state: str = "disabled",
    ) -> bool:
        """Stop a running plugin and remove every registration it made."""
        async with self._lock:
            key = self.key(slug, scope)
            rp = self._running.pop(key, None)
            if rp is None:
                if installation is not None:
                    installation.state = new_state
                return False
            for task in rp.tasks:
                task.cancel()
            if rp.tasks:
                await asyncio.gather(*rp.tasks, return_exceptions=True)
            try:
                await rp.instance.on_stop(rp.context)
            except Exception:  # pragma: no cover - never block shutdown
                log.exception("plugin %s on_stop failed", slug)
            await rp.context.teardown()
            self.commands.unregister_plugin(slug)
            if rp.router is not None and rp.router.parent_router is not None:
                rp.router.parent_router.sub_routers.remove(rp.router)
            rp.instance.running = False
            if installation is not None:
                installation.state = new_state
                installation.enabled_at = None
                await self._write_log(installation, "info", f"plugin stopped ({new_state})")
            log.info("plugin %s stopped in %s", slug, scope.key)
            return True

    async def restart(
        self,
        slug: str,
        *,
        installation: PluginInstallation,
        scope: PluginScope,
        bot: Bot | None = None,
        dispatcher: Dispatcher | None = None,
    ) -> RunningPlugin:
        await self.stop(slug, scope, installation=installation, dispatcher=dispatcher)
        installation.state = "configured"
        return await self.start(
            slug,
            installation=installation,
            scope=scope,
            bot=bot,
            dispatcher=dispatcher,
        )

    async def stop_all(self, dispatcher: Dispatcher | None = None) -> None:
        for rp in list(self._running.values()):
            await self.stop(rp.slug, rp.scope, dispatcher=dispatcher)

    async def reconcile(
        self,
        *,
        bot_id: int | None,
        group_id: int | None,
        installs: Iterable[PluginInstallation],
        bot: Bot | None = None,
        dispatcher: Dispatcher | None = None,
        slugs: Iterable[str] | None = None,
        plugin_ids: "dict[str, int] | None" = None,
        grants: "dict[int, list[str]] | None" = None,
    ) -> dict[str, str]:
        """Make the runtime match the database: start what should run, stop the rest.

        This is the single entry point used by bot boot, clone workers and the
        webapp's enable/disable actions.
        """
        scope = PluginScope(bot_id=bot_id, group_id=group_id)
        wanted = set(slugs) if slugs is not None else {p.slug for p in self.plugins.manifests()}
        installs = list(installs)
        result: dict[str, str] = {}
        for slug in wanted:
            install = self.resolve_scope(installs, slug, bot_id, group_id, plugin_ids)
            if install is not None and install.state in ENABLED_STATES:
                try:
                    await self.start(
                        slug,
                        installation=install,
                        scope=scope,
                        bot=bot,
                        dispatcher=dispatcher,
                        granted_permissions=(grants or {}).get(install.id),
                    )
                    result[slug] = "running"
                except PluginLifecycleError as exc:
                    result[slug] = f"error: {exc}"
                    log.warning("could not start plugin %s: %s", slug, exc)
                except PluginError as exc:  # permission/start-up failure is not fatal
                    result[slug] = f"error: {exc}"
                    log.warning("plugin %s failed to start: %s", slug, exc)
            else:
                result[slug] = install.state if install else "not_installed"
        for key, rp in list(self._running.items()):
            if rp.scope.key != scope.key:
                continue
            install = self.resolve_scope(installs, rp.slug, bot_id, group_id, plugin_ids)
            if install is None or install.state not in ENABLED_STATES:
                await self.stop(rp.slug, scope, installation=install, dispatcher=dispatcher)
                result[rp.slug] = "stopped"
        return result

    # -- helpers ---------------------------------------------------------
    def _register_commands(self, manifest: PluginManifest) -> None:
        """Publish a running plugin's declared commands (idempotent, non-fatal)."""
        from meiko.core.commands import Command, CommandError

        for name in manifest.commands or []:
            clean = str(name).lstrip("/").split("@")[0].lower()
            if not clean:
                continue
            existing = self.commands.get(clean)
            if existing is not None:
                if existing.plugin not in (None, manifest.slug):
                    log.warning(
                        "plugin %s command /%s clashes with %s; keeping the original",
                        manifest.slug,
                        clean,
                        existing.plugin or "core",
                    )
                continue
            try:
                self.commands.register(
                    Command(
                        name=clean,
                        description=f"{manifest.name} plugin command",
                        plugin=manifest.slug,
                        category=manifest.category,
                    )
                )
            except CommandError as exc:  # pragma: no cover - defensive
                log.debug("could not register /%s for %s: %s", clean, manifest.slug, exc)

    async def _write_log(
        self, installation: PluginInstallation, level: str, message: str
    ) -> None:
        if installation.id is None:
            return
        # Observability side effect: under test we keep it in-process so an open
        # test transaction (SQLite single-writer) can never block the lifecycle.
        if get_settings().is_testing and not self.persist_logs:
            log.debug("[%s] %s", level, message)
            return
        try:
            async with get_sessionmaker()() as session:
                session.add(
                    PluginLog(installation_id=installation.id, level=level, message=message[:2000])
                )
                await session.commit()
        except Exception:  # pragma: no cover - logging must not break lifecycle
            log.debug("could not persist plugin log", exc_info=True)


def manifest_from_plugin_row(plugin: Plugin) -> dict[str, Any]:
    """Rebuild a manifest dict from the DB row (used by API responses)."""
    return {
        "slug": plugin.slug,
        "name": plugin.name,
        "version": plugin.version,
        "description": plugin.description,
        "author": plugin.author,
        "category": plugin.category,
        "icon": plugin.icon,
        "permissions": list(plugin.permissions or []),
        "dependencies": list(plugin.dependencies or []),
        "config_schema": plugin.config_schema or {},
        "manifest": plugin.manifest or {},
    }
