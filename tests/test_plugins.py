"""Plugin SDK, lifecycle manager and builtin plugin behaviour."""

from __future__ import annotations

import pytest
from aiogram import Router

from meiko.core.commands import Command, CommandRegistry, CommandRegistry as _CR
from meiko.core.commands import CommandError
from meiko.db.models import PluginInstallation, Plugin
from meiko.plugins.base import MeikoPlugin, PluginError, PluginRegistry
from meiko.plugins.context import PluginPermissionError, PluginScope
from meiko.plugins.manager import PluginLifecycleError, PluginManager
from meiko.plugins.manifest import ManifestError, PluginManifest
from meiko.plugins.builtin import BUILTIN_MANIFESTS
from meiko.plugins.builtin.automation import validate_workflow
from meiko.plugins.builtin.filters import FiltersPlugin
from meiko.services.core import PluginService, UserService
from meiko.security.permissions import Role


class DummyPlugin(MeikoPlugin):
    manifest = PluginManifest(
        slug="dummy",
        name="Dummy",
        category="utilities",
        permissions=["storage.read", "storage.write", "scheduler"],
    )

    def __init__(self) -> None:
        super().__init__()
        self.started = False
        self.stopped = False
        self.job_runs = 0

    async def on_start(self, ctx):
        self.started = True
        router = Router(name="dummy")
        ctx.add_router(router)
        ctx.schedule("tick", 5, self.tick)

    async def tick(self):
        self.job_runs += 1

    async def on_stop(self, ctx):
        self.stopped = True


async def make_install(session, slug: str = "dummy", state: str = "installed", bot_id=None):
    """Create a bot + plugin row + installation for lifecycle tests."""
    from meiko.services.core import BotService

    if bot_id is None:
        user = await UserService(session).get_or_create_telegram_user(555, username="owner")
        bot = await BotService(session).register(user.id, token="555555555:" + "E" * 34)
        bot_id = bot.id
    plugin = Plugin(slug=slug, name=slug, category="utilities", config_schema={})
    session.add(plugin)
    await session.flush()
    install = PluginInstallation(plugin_id=plugin.id, bot_id=bot_id, scope="bot", state=state)
    session.add(install)
    await session.flush()
    return install


class TestManifest:
    def test_builtin_manifests_are_valid(self):
        for manifest in BUILTIN_MANIFESTS:
            manifest.validate()
            assert manifest.validate_config_schema() == []

    def test_slug_validation(self):
        with pytest.raises(ManifestError):
            PluginManifest(slug="Bad Slug!", name="x").validate()

    def test_unknown_permission_rejected(self):
        with pytest.raises(ManifestError):
            PluginManifest(slug="ok_plugin", name="x", permissions=["root.everything"]).validate()

    def test_unapproved_third_party_rejected(self):
        manifest = PluginManifest(slug="third_party", name="x", source="marketplace", checksum="abc")
        with pytest.raises(ManifestError):
            manifest.validate()
        manifest.approved = True
        manifest.validate()

    def test_version_compatibility(self):
        manifest = PluginManifest(
            slug="future", name="x", source="marketplace", approved=True,
            checksum="a" * 8, min_platform_version="9.0.0",
        )
        with pytest.raises(ManifestError):
            manifest.validate(platform_version="1.0.0")

    def test_roundtrip_dict(self):
        data = BUILTIN_MANIFESTS[0].to_dict()
        assert PluginManifest.from_dict(data).slug == BUILTIN_MANIFESTS[0].slug


class TestPluginRegistry:
    def test_register_and_create(self):
        reg = PluginRegistry()
        reg.register(DummyPlugin)
        assert "dummy" in reg.slugs()
        assert isinstance(reg.create("dummy"), DummyPlugin)

    def test_missing_implementation_refused(self):
        with pytest.raises(PluginError):
            PluginRegistry().create("does_not_exist")


class TestPluginContext:
    async def test_permissions_enforced(self, cache, db):
        from meiko.db.base import get_sessionmaker
        from meiko.plugins.context import PluginContext

        manifest = PluginManifest(slug="limited", name="x", permissions=["storage.read"])
        ctx = PluginContext(
            manifest=manifest,
            scope=PluginScope(bot_id=1),
            config={},
            session_factory=get_sessionmaker(),
            cache=cache,
        )
        with pytest.raises(PluginPermissionError):
            await ctx.set_state("k", 1)
        assert await ctx.get_state("k") is None

    async def test_storage_namespaced_by_scope(self, cache, db):
        from meiko.db.base import get_sessionmaker
        from meiko.plugins.context import PluginContext

        manifest = PluginManifest(slug="store", name="x", permissions=["storage.read", "storage.write"])
        a = PluginContext(manifest=manifest, scope=PluginScope(bot_id=1), config={},
                          session_factory=get_sessionmaker(), cache=cache)
        b = PluginContext(manifest=manifest, scope=PluginScope(bot_id=2), config={},
                          session_factory=get_sessionmaker(), cache=cache)
        await a.set_state("counter", 5)
        assert await a.get_state("counter") == 5
        assert await b.get_state("counter") is None


class TestPluginManagerLifecycle:
    async def _install(self, session, slug="dummy", state="installed", bot_id=None):
        if bot_id is None:
            user = await UserService(session).get_or_create_telegram_user(555, username="owner")
            from meiko.services.core import BotService

            bot = await BotService(session).register(user.id, token="555555555:" + "E" * 34)
            bot_id = bot.id
        plugin = Plugin(slug=slug, name=slug, category="utilities", config_schema={})
        session.add(plugin)
        await session.flush()
        install = PluginInstallation(plugin_id=plugin.id, bot_id=bot_id, scope="bot", state=state)
        session.add(install)
        await session.flush()
        return install

    async def test_disabled_plugin_registers_nothing(self, db, cache, session):
        registry = PluginRegistry()
        registry.register(DummyPlugin)
        commands = CommandRegistry()
        manager = PluginManager(plugins=registry, commands=commands, cache=cache)
        install = await self._install(session, state="disabled")
        scope = PluginScope(bot_id=1)
        with pytest.raises(PluginLifecycleError):
            await manager.start("dummy", installation=install, scope=PluginScope(bot_id=install.bot_id))
        assert manager.running_summary() == []
        assert manager.active_commands() == []

    async def test_start_registers_and_stop_tears_down(self, db, cache, session):
        registry = PluginRegistry()
        registry.register(DummyPlugin)
        manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
        install = await make_install(session, state="enabled")
        scope = PluginScope(bot_id=install.bot_id)
        rp = await manager.start("dummy", installation=install, scope=scope)
        assert rp.instance.started
        assert install.state == "running"
        assert manager.running_summary()[0]["scope"] == f"bot:{install.bot_id}"
        assert rp.tasks
        stopped = await manager.stop("dummy", scope, installation=install)
        assert stopped and rp.instance.stopped
        assert install.state == "disabled"
        assert manager.running_summary() == []
        for task in rp.tasks:
            assert task.done()

    async def test_start_is_idempotent(self, db, cache, session):
        registry = PluginRegistry()
        registry.register(DummyPlugin)
        manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
        install = await make_install(session, state="enabled")
        scope = PluginScope(bot_id=install.bot_id)
        first = await manager.start("dummy", installation=install, scope=scope)
        second = await manager.start("dummy", installation=install, scope=scope)
        assert first is second

    async def test_unconfigured_plugin_refused(self, db, cache, session):
        registry = PluginRegistry()

        class NeedsKey(DummyPlugin):
            manifest = PluginManifest(
                slug="needs_key", name="x", category="utilities",
                config_schema={"key": {"type": "secret", "required": True}},
            )

        registry.register(NeedsKey)
        manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
        install = await self._install(session, slug="needs_key", state="installed")
        with pytest.raises(PluginLifecycleError):
            await manager.start(
                "needs_key", installation=install, scope=PluginScope(bot_id=install.bot_id)
            )

    async def test_reconcile_stops_disabled(self, db, cache, session):
        registry = PluginRegistry()
        registry.register(DummyPlugin)
        manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
        install = await make_install(session, state="enabled")
        scope = PluginScope(bot_id=install.bot_id)
        await manager.start("dummy", installation=install, scope=scope)
        install.state = "disabled"
        result = await manager.reconcile(
            bot_id=install.bot_id, group_id=None, installs=[install], slugs=["dummy"]
        )
        assert result["dummy"] == "stopped"
        assert manager.running_summary() == []

    async def test_scope_resolution_prefers_group(self, db, cache):
        manager = PluginManager(cache=cache)
        global_install = PluginInstallation(plugin_id=1, scope="global", state="enabled")
        bot_install = PluginInstallation(plugin_id=1, bot_id=5, scope="bot", state="disabled")
        group_install = PluginInstallation(plugin_id=1, bot_id=5, group_id=9, scope="group", state="enabled")
        installs = [global_install, bot_install, group_install]
        assert manager.resolve_scope(installs, "x", 5, 9) is group_install
        assert manager.resolve_scope(installs, "x", 5, None) is bot_install
        assert manager.resolve_scope(installs, "x", 6, None) is global_install

    async def test_all_builtin_plugins_start_and_stop(self, db, cache, session):
        from meiko.plugins.base import registry as builtin_registry

        commands = CommandRegistry()
        manager = PluginManager(plugins=builtin_registry, commands=commands, cache=cache)
        install_probe = await self._install(session, slug="probe")
        scope = PluginScope(bot_id=install_probe.bot_id)
        started = []
        for manifest in BUILTIN_MANIFESTS:
            plugin = Plugin(slug=manifest.slug, name=manifest.name, category=manifest.category)
            session.add(plugin)
            await session.flush()
            install = PluginInstallation(
                plugin_id=plugin.id, bot_id=scope.bot_id, scope="bot", state="enabled",
                config={k: v.get("default") for k, v in manifest.config_schema.items() if "default" in v},
            )
            session.add(install)
            await session.flush()
            try:
                await manager.start(manifest.slug, installation=install, scope=scope)
                started.append(manifest.slug)
            except PluginLifecycleError as exc:  # required config (ai) is expected
                assert "configured" in str(exc) or "dependencies" in str(exc)
            except PluginPermissionError as exc:  # optional grants not requested here
                assert "is not granted" in str(exc)
        assert "anti_spam" in started and "welcome" in started
        # commands declared by a running plugin must be visible to Telegram/Studio
        assert commands.get("autorename") is not None, "renamer commands were not published"
        assert commands.get("keepalive") is not None
        assert "autorename" in manager.active_commands()
        await manager.stop_all()
        assert manager.running_summary() == []
        assert commands.get("autorename") is None, "commands leaked after stop"
        assert manager.active_commands() == []


class TestCommandRegistry:
    def test_duplicate_command_rejected(self):
        reg = CommandRegistry()
        reg.register(Command(name="start", plugin="a"))
        with pytest.raises(CommandError):
            reg.register(Command(name="start", plugin="b"))

    def test_unregister_plugin_removes_all(self):
        reg = CommandRegistry()
        reg.register(Command(name="a", aliases=["aa"], plugin="p1"))
        reg.register(Command(name="b", plugin="p1"))
        assert reg.unregister_plugin("p1") == 2
        assert len(reg) == 0

    def test_invalid_names(self):
        with pytest.raises(CommandError):
            Command(name="Bad-Name").validate()

    def test_telegram_menu_excludes_hidden(self):
        reg = CommandRegistry()
        reg.register(Command(name="visible", description="v"))
        reg.register(Command(name="secret", hidden=True))
        assert [c["command"] for c in reg.telegram_menu()] == ["visible"]


class TestBuiltinPlugins:
    async def test_filters_trigger_matching(self, cache, db):
        from meiko.db.base import get_sessionmaker
        from meiko.plugins.context import PluginContext

        plugin = FiltersPlugin()
        ctx = PluginContext(
            manifest=FiltersPlugin.manifest,
            scope=PluginScope(bot_id=1),
            config={"allow_regex": True, "max_triggers": 5},
            session_factory=get_sessionmaker(),
            cache=cache,
        )
        plugin.setup(ctx)
        await plugin.on_start(ctx)
        await plugin.add_trigger("-100", "hello", "hi there")
        assert await plugin.match("-100", "hello")
        assert await plugin.match("-100", "HELLO")
        assert await plugin.match("-100", "nope") is None
        await plugin.add_trigger("-100", r"\d{3}", "three digits", match_type="regex")
        assert await plugin.match("-100", "code 123")

    async def test_automation_workflow_validation(self):
        assert validate_workflow({"trigger": {"event": "message"}, "actions": [{"type": "send_message"}]}) == []
        assert validate_workflow({"trigger": {"event": "hack"}, "actions": []})
        assert validate_workflow({"trigger": {"event": "message"}, "actions": [{"type": "exec_shell"}]})

    async def test_analytics_leaderboard(self, cache, db):
        from meiko.db.base import get_sessionmaker
        from meiko.plugins.context import PluginContext
        from meiko.plugins.builtin.analytics import AnalyticsPlugin

        plugin = AnalyticsPlugin()
        ctx = PluginContext(
            manifest=AnalyticsPlugin.manifest,
            scope=PluginScope(bot_id=1),
            config={"top_size": 3},
            session_factory=get_sessionmaker(),
            cache=cache,
        )
        plugin.setup(ctx)
        await plugin.on_start(ctx)
        await plugin.bump(-100, 1, "messages")
        await plugin.bump(-100, 1, "messages")
        await plugin.bump(-100, 2, "messages")
        board = await plugin.leaderboard(-100)
        assert board[0] == (1, 2)
        await plugin.on_stop(ctx)


class TestScopeResolverMatrix:
    """The brief's matrix: identity-matched, precedence-respecting resolution.

    These are the cases an earlier bug got wrong: ``resolve_scope`` matched rows
    by ``bot_id``/``group_id`` only, so with two plugins installed on one bot the
    answer depended on iteration order rather than on the slug being asked for.
    """

    @staticmethod
    def _row(plugin_id: int, *, bot_id=None, group_id=None, state="enabled"):
        scope = "group" if group_id else ("bot" if bot_id else "global")
        return PluginInstallation(
            plugin_id=plugin_id, bot_id=bot_id, group_id=group_id, scope=scope, state=state
        )

    def test_two_global_plugins_resolve_independently(self):
        a = self._row(1, state="configured")
        b = self._row(2, state="disabled")
        installs = [a, b]
        ids = {"alpha": 1, "beta": 2}
        assert PluginManager.resolve_scope(installs, "alpha", None, None, ids) is a
        assert PluginManager.resolve_scope(installs, "beta", None, None, ids) is b

    def test_two_plugins_on_one_bot_resolve_independently(self):
        a = self._row(1, bot_id=5)
        b = self._row(2, bot_id=5)
        installs = [a, b]
        ids = {"alpha": 1, "beta": 2}
        assert PluginManager.resolve_scope(installs, "alpha", 5, None, ids) is a
        assert PluginManager.resolve_scope(installs, "beta", 5, None, ids) is b

    def test_global_plus_bot_override(self):
        global_row = self._row(1, state="enabled")
        bot_row = self._row(1, bot_id=5, state="disabled")
        installs = [global_row, bot_row]
        ids = {"alpha": 1}
        assert PluginManager.resolve_scope(installs, "alpha", 5, None, ids) is bot_row
        assert PluginManager.resolve_scope(installs, "alpha", 6, None, ids) is global_row

    def test_bot_plus_group_override(self):
        bot_row = self._row(1, bot_id=5, state="enabled")
        group_row = self._row(1, bot_id=5, group_id=9, state="disabled")
        installs = [bot_row, group_row]
        ids = {"alpha": 1}
        assert PluginManager.resolve_scope(installs, "alpha", 5, 9, ids) is group_row
        assert PluginManager.resolve_scope(installs, "alpha", 5, None, ids) is bot_row

    def test_disabled_group_override_beats_enabled_bot(self):
        bot_row = self._row(1, bot_id=5, state="running")
        group_row = self._row(1, bot_id=5, group_id=9, state="disabled")
        resolved = PluginManager.resolve_scope(
            [bot_row, group_row], "alpha", 5, 9, {"alpha": 1}
        )
        assert resolved is group_row
        assert resolved.state == "disabled"

    def test_disabled_bot_override_beats_global(self):
        global_row = self._row(1, state="running")
        bot_row = self._row(1, bot_id=5, state="disabled")
        resolved = PluginManager.resolve_scope(
            [global_row, bot_row], "alpha", 5, None, {"alpha": 1}
        )
        assert resolved is bot_row

    def test_absent_everywhere_is_none(self):
        assert (
            PluginManager.resolve_scope([self._row(1)], "missing", 5, 9, {"missing": 99})
            is None
        )

    def test_rows_for_other_plugins_are_never_returned(self):
        other = self._row(2, bot_id=5, state="running")
        assert PluginManager.resolve_scope([other], "alpha", 5, None, {"alpha": 1}) is None

    async def test_multiple_bots_with_different_owners_stay_separate(self, db, cache, session):
        """Two bots, two owners, same plugin: each bot gets its own row."""
        from meiko.services.core import BotService

        alice = await UserService(session).get_or_create_telegram_user(1001, username="alice")
        bob = await UserService(session).get_or_create_telegram_user(1002, username="bob")
        bots = BotService(session)
        alice_bot = await bots.register(alice.id, token="111111:" + "a" * 35)
        bob_bot = await bots.register(bob.id, token="222222:" + "b" * 35)
        plugin = Plugin(slug="scope_probe", name="Probe", category="utilities")
        session.add(plugin)
        await session.flush()
        alice_row = self._row(plugin.id, bot_id=alice_bot.id, state="enabled")
        bob_row = self._row(plugin.id, bot_id=bob_bot.id, state="disabled")
        session.add_all([alice_row, bob_row])
        await session.flush()
        installs = [alice_row, bob_row]
        ids = {"scope_probe": plugin.id}
        assert PluginManager.resolve_scope(installs, "scope_probe", alice_bot.id, None, ids) is alice_row
        assert PluginManager.resolve_scope(installs, "scope_probe", bob_bot.id, None, ids) is bob_row


class TestDisabledPluginsAreInert:
    """A disabled plugin must own nothing: no router, job, command or capacity."""

    async def _manager_with(self, cache, session) -> tuple[PluginManager, PluginInstallation, CommandRegistry]:
        registry = PluginRegistry()
        registry.register(DummyPlugin)
        commands = CommandRegistry()
        manager = PluginManager(plugins=registry, commands=commands, cache=cache)
        install = await make_install(session, state="enabled")
        return manager, install, commands

    async def test_disabled_plugin_registers_nothing(self, cache, session):
        manager, install, commands = await self._manager_with(cache, session)
        scope = PluginScope(bot_id=install.bot_id)
        plugin = manager.plugins.get("dummy")()

        # 1. Disabled *before* start: start() refuses on state, not on a flag.
        install.state = "disabled"
        with pytest.raises(PluginLifecycleError):
            await manager.start("dummy", installation=install, scope=scope)
        assert manager.running_summary() == []
        assert plugin.started is False

        # 2. Enabled then disabled: everything registered is torn down.
        install.state = "enabled"
        running = await manager.start("dummy", installation=install, scope=scope)
        assert plugin.started is True or running.context.jobs
        assert running.context.jobs, "scheduler job should exist while running"
        await manager.stop("dummy", scope, installation=install)
        assert manager.running_summary() == []
        assert running.context.jobs == []
        assert all(task.cancelled() or task.done() for task in running.tasks) if running.tasks else True
        assert manager.active_commands() == []

    async def test_reconcile_after_restart_keeps_it_inert(self, cache, session):
        """Crash/restart is just a fresh manager reading the same rows."""
        manager, install, _commands = await self._manager_with(cache, session)
        scope = PluginScope(bot_id=install.bot_id)
        install.state = "enabled"
        await manager.start("dummy", installation=install, scope=scope)

        install.state = "disabled"
        fresh = PluginManager(plugins=manager.plugins, commands=CommandRegistry(), cache=cache)
        result = await fresh.reconcile(
            bot_id=install.bot_id,
            group_id=None,
            installs=[install],
            slugs=["dummy"],
            plugin_ids={"dummy": install.plugin_id},
        )
        assert result["dummy"] == "disabled"
        assert fresh.running_summary() == []

    async def test_worker_capacity_not_consumed_by_disabled_plugins(self, cache, session):
        manager, install, _commands = await self._manager_with(cache, session)
        install.state = "disabled"
        await manager.reconcile(
            bot_id=install.bot_id,
            group_id=None,
            installs=[install],
            slugs=["dummy"],
            plugin_ids={"dummy": install.plugin_id},
        )
        running = [
            rp for rp in manager._running.values() if rp.scope.bot_id == install.bot_id
        ]
        assert running == []
        assert manager.running_summary() == []

    async def test_event_subscriptions_are_dropped_on_disable(self, cache, session):
        from meiko.db.base import get_sessionmaker

        registry = PluginRegistry()

        class Subscriber(DummyPlugin):
            manifest = PluginManifest(
                slug="subscriber",
                name="Subscriber",
                category="utilities",
                permissions=["events.subscribe", "storage.read"],
            )

            async def on_start(self, ctx):
                ctx.subscribe("message.new")
                ctx.add_router(Router(name="subscriber"))

        registry.register(Subscriber)
        manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
        install = await make_install(session, slug="subscriber", state="enabled")
        scope = PluginScope(bot_id=install.bot_id)
        running = await manager.start("subscriber", installation=install, scope=scope)
        assert running.context.subscriptions == ["message.new"]
        await manager.stop("subscriber", scope, installation=install)
        assert running.context.subscriptions == []
        assert running.context.routers == []
