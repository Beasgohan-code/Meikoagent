"""Service layer tests: ownership, tenancy, plugin lifecycle, moderation."""

from __future__ import annotations

import pytest

from meiko.db.models import Plugin, PluginInstallation
from meiko.services.core import (
    BotService,
    Conflict,
    DashboardService,
    GroupService,
    ModerationService,
    NotFound,
    PluginService,
    UserService,
    ValidationError,
)

VALID_TOKEN = "123456789:" + "A" * 34


async def make_user(session, telegram_id=100):
    return await UserService(session).get_or_create_telegram_user(
        telegram_id, username=f"user{telegram_id}", first_name="Meiko"
    )


class TestUserService:
    async def test_get_or_create_is_idempotent(self, session):
        service = UserService(session)
        first = await service.get_or_create_telegram_user(5, username="meiko", first_name="M")
        second = await service.get_or_create_telegram_user(5, username="meiko", first_name="M")
        assert first.id == second.id
        assert second.last_seen_at is not None

    async def test_only_owner_changes_roles(self, session):
        service = UserService(session)
        user = await make_user(session)
        with pytest.raises(PermissionError):
            await service.set_role(user.id, "owner", actor_is_owner=False)
        promoted = await service.set_role(user.id, "owner", actor_is_owner=True)
        assert promoted.is_owner is True


class TestBotService:
    async def test_register_encrypts_token(self, session):
        user = await make_user(session)
        service = BotService(session)
        bot = await service.register(user.id, token=VALID_TOKEN, name="Meiko")
        assert "A" * 34 not in bot.token_encrypted
        assert bot.token_encrypted.startswith("enc:v1:")
        assert service.token(bot) == VALID_TOKEN
        assert bot.token_fingerprint

    async def test_duplicate_token_rejected(self, session):
        user = await make_user(session)
        service = BotService(session)
        await service.register(user.id, token=VALID_TOKEN)
        with pytest.raises(Conflict):
            await service.register(user.id, token=VALID_TOKEN)

    async def test_invalid_tokens_rejected(self, session):
        user = await make_user(session)
        service = BotService(session)
        for bad in ("not-a-token", "abc:def", "123:short", "123:AA BB" + "A" * 30):
            with pytest.raises(ValidationError):
                await service.register(user.id, token=bad)

    async def test_tenant_isolation_on_get(self, session):
        owner = await make_user(session, 1)
        stranger = await make_user(session, 2)
        service = BotService(session)
        bot = await service.register(owner.id, token=VALID_TOKEN)
        with pytest.raises(NotFound):
            await service.get(bot.id, stranger.id)
        assert await service.get(bot.id, owner.id) is bot

    async def test_list_for_owner_excludes_others(self, session):
        owner = await make_user(session, 1)
        other = await make_user(session, 2)
        service = BotService(session)
        await service.register(owner.id, token=VALID_TOKEN)
        await service.register(other.id, token="987654321:" + "B" * 34)
        assert len(await service.list_for_owner(owner.id)) == 1

    async def test_status_transitions(self, session):
        user = await make_user(session)
        service = BotService(session)
        bot = await service.register(user.id, token=VALID_TOKEN)
        await service.verify_identity(bot, telegram_bot_id=123456789, username="meiko_bot")
        assert bot.token_valid and bot.status == "stopped"
        await service.set_status(bot, "running")
        assert bot.last_started_at is not None
        await service.set_status(bot, "error", error="boom")
        assert bot.last_error == "boom"
        with pytest.raises(ValidationError):
            await service.set_status(bot, "nonsense")

    async def test_token_replacement(self, session):
        user = await make_user(session)
        service = BotService(session)
        bot = await service.register(user.id, token=VALID_TOKEN)
        new_token = "111111111:" + "C" * 34
        await service.replace_token(bot, new_token)
        assert service.token(bot) == new_token
        assert bot.token_valid is False

    async def test_clone_inherits_settings_not_runtime(self, session):
        user = await make_user(session)
        service = BotService(session)
        source = await service.register(user.id, token=VALID_TOKEN, settings={"lang": "en"})
        await service.set_status(source, "running")
        clone = await service.clone_from(
            source, user.id, token="222222222:" + "D" * 34, name="Clone"
        )
        assert clone.is_clone and clone.source_bot_id == source.id
        assert clone.settings == {"lang": "en"}
        assert clone.status == "draft"
        assert clone.id != source.id


class TestGroupAndModeration:
    async def test_upsert_group_creates_settings(self, session):
        user = await make_user(session)
        bots = BotService(session)
        bot = await bots.register(user.id, token=VALID_TOKEN)
        groups = GroupService(session)
        group = await groups.upsert(bot.id, -100123, title="Cyber")
        settings = await groups.settings(group.id)
        assert settings.welcome_enabled is True

    async def test_update_settings_whitelists_columns(self, session):
        user = await make_user(session)
        bot = await BotService(session).register(user.id, token=VALID_TOKEN)
        groups = GroupService(session)
        group = await groups.upsert(bot.id, -100123, title="G")
        await groups.update_settings(group.id, welcome_text="hi", id=999, nonsense=1)
        settings = await groups.settings(group.id)
        assert settings.welcome_text == "hi"

    async def test_moderation_history_and_stats(self, session):
        user = await make_user(session)
        bot = await BotService(session).register(user.id, token=VALID_TOKEN)
        group = await GroupService(session).upsert(bot.id, -1001, title="G")
        service = ModerationService(session)
        await service.record(bot.id, "ban", group_id=group.id, target_id=7, reason="spam")
        await service.record(bot.id, "warn", group_id=group.id, target_id=8)
        history = await service.history(bot.id, group_id=group.id)
        assert len(history) == 2
        stats = await service.stats(bot.id)
        assert stats["by_action"]["ban"] == 1
        assert stats["total"] == 2


class TestPluginService:
    async def _plugin(self, session, slug="anti_spam", **kwargs):
        plugin = Plugin(
            slug=slug,
            name=slug.replace("_", " ").title(),
            category="moderation",
            config_schema={
                "threshold": {"type": "int", "default": 3},
                "api_key": {"type": "string", "required": True},
            },
            dependencies=kwargs.pop("dependencies", []),
            **kwargs,
        )
        session.add(plugin)
        await session.flush()
        return plugin

    async def test_install_sets_defaults(self, session):
        plugin = await self._plugin(session)
        service = PluginService(session)
        install = await service.install(plugin.slug, bot_id=None, user_id=1)
        assert install.state == "installed"
        assert install.config["threshold"] == 3
        assert install.scope == "global"

    async def test_install_is_idempotent(self, session):
        plugin = await self._plugin(session)
        service = PluginService(session)
        first = await service.install(plugin.slug, bot_id=None, user_id=1)
        second = await service.install(plugin.slug, bot_id=None, user_id=1)
        assert first.id == second.id

    async def test_missing_required_config_blocks_enable(self, session):
        plugin = await self._plugin(session)
        service = PluginService(session)
        install = await service.install(plugin.slug, bot_id=None, user_id=1)
        with pytest.raises(ValidationError):
            await service.enable(install)
        await service.configure(install, {"api_key": "secret"})
        await service.enable(install)
        assert install.state == "configured"

    async def test_unknown_config_key_rejected(self, session):
        plugin = await self._plugin(session)
        service = PluginService(session)
        install = await service.install(plugin.slug, bot_id=None, user_id=1)
        with pytest.raises(ValidationError):
            await service.configure(install, {"nope": 1})

    async def test_config_type_coercion(self, session):
        plugin = await self._plugin(session)
        service = PluginService(session)
        install = await service.install(plugin.slug, bot_id=None, user_id=1)
        await service.configure(install, {"threshold": "9", "api_key": "k"})
        assert install.config["threshold"] == 9

    async def test_dependency_must_be_enabled(self, session):
        await self._plugin(session, slug="welcome")
        plugin = await self._plugin(session, slug="reports", dependencies=["welcome"])
        service = PluginService(session)
        with pytest.raises(ValidationError):
            await service.install(plugin.slug, bot_id=None, user_id=1)
        dep = await service.install("welcome", bot_id=None, user_id=1)
        await service.configure(dep, {"api_key": "k"})
        await service.enable(dep)
        install = await service.install(plugin.slug, bot_id=None, user_id=1)
        assert install.state == "installed"

    async def test_invalid_transition_rejected(self, session):
        plugin = await self._plugin(session, slug="analytics")
        service = PluginService(session)
        install = await service.install(plugin.slug, bot_id=None, user_id=1)
        with pytest.raises(ValidationError):
            await service.transition(install, "configured")
        with pytest.raises(ValidationError):
            await service.transition(install, "nonsense")

    async def test_unapproved_plugin_cannot_install(self, session):
        await self._plugin(session, slug="sketchy", status="blocked")
        with pytest.raises(ValidationError):
            await PluginService(session).install("sketchy", bot_id=None, user_id=1)


class TestDashboard:
    async def test_overview_reports_real_counts(self, session):
        user = await make_user(session)
        bots = BotService(session)
        bot = await bots.register(user.id, token=VALID_TOKEN)
        await bots.set_status(bot, "running")
        await GroupService(session).upsert(bot.id, -1009, title="G")
        plugin = Plugin(
            slug="welcome", name="Welcome", category="moderation", config_schema={}
        )
        session.add(plugin)
        await session.flush()
        service = PluginService(session)
        install = await service.install("welcome", bot_id=bot.id, user_id=user.id)
        await service.transition(install, "enabled")
        overview = await DashboardService(session).overview(user.id)
        assert overview["bots"]["total"] == 1
        assert overview["bots"]["running"] == 1
        assert overview["groups"] == 1
        assert overview["plugins_enabled"] == 1
        assert len(overview["activity_series"]) == 7
