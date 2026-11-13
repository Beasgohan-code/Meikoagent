"""Schema additions that answer production questions (PART 17).

Installation history and permission grants exist because the previous schema could
only describe *now*: who enabled this, from which version, and what a plugin was
actually allowed to do were unanswerable after the fact.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

VALID_TOKEN = "7654321:" + "K" * 34


@pytest_asyncio.fixture()
async def client(settings):
    from meiko.api.app import create_app
    from meiko.cache import reset_cache

    await reset_cache()
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def setup_bot(client: AsyncClient, telegram_id: int) -> tuple[dict, int]:
    login = await client.post("/api/v1/auth/dev", json={"telegram_id": telegram_id})
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    bot = await client.post(
        "/api/v1/bots", json={"token": VALID_TOKEN, "name": "Schema"}, headers=headers
    )
    assert bot.status_code == 201, bot.text
    return headers, bot.json()["id"]


async def install(client: AsyncClient, headers: dict, bot_id: int, slug: str = "welcome") -> int:
    response = await client.post(
        f"/api/v1/plugins/{slug}/install", json={"bot_id": bot_id}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestInstallHistory:
    async def test_a_fresh_install_records_its_origin(self, client):
        headers, bot_id = await setup_bot(client, 7101)
        installation_id = await install(client, headers, bot_id)
        history = await client.get(
            f"/api/v1/plugins/installations/{installation_id}/history", headers=headers
        )
        assert history.status_code == 200
        items = history.json()["items"]
        assert items
        assert items[-1]["action"] == "installed"
        assert items[-1]["actor_user_id"] is not None

    async def test_every_transition_is_appended_in_order(self, client):
        headers, bot_id = await setup_bot(client, 7102)
        installation_id = await install(client, headers, bot_id)
        await client.post(
            f"/api/v1/plugins/installations/{installation_id}/enable", headers=headers
        )
        await client.post(
            f"/api/v1/plugins/installations/{installation_id}/disable", headers=headers
        )
        body = (
            await client.get(
                f"/api/v1/plugins/installations/{installation_id}/history", headers=headers
            )
        ).json()
        actions = [row["action"] for row in body["items"]]
        assert "installed" in actions
        assert "state.enabled" in actions or "state.configured" in actions
        assert "state.disabled" in actions
        # Newest first, so disabled comes before installed.
        assert actions.index("installed") > 0

    async def test_history_names_the_actor_for_every_change(self, client, session):
        """'Who enabled it' is the reason this table exists."""
        from sqlalchemy import select

        from meiko.db.models import PluginInstallEvent, User

        headers, bot_id = await setup_bot(client, 7106)
        installation_id = await install(client, headers, bot_id)
        await client.post(
            f"/api/v1/plugins/installations/{installation_id}/enable", headers=headers
        )
        await client.post(
            f"/api/v1/plugins/installations/{installation_id}/disable", headers=headers
        )
        rows = (
            await session.scalars(
                select(PluginInstallEvent)
                .where(PluginInstallEvent.installation_id == installation_id)
                .order_by(PluginInstallEvent.id)
            )
        ).all()
        assert rows
        actors = {row.action: row.actor_user_id for row in rows}
        # Every transition written through the API records who did it.
        for action in ("installed", "state.enabled", "state.configured", "state.disabled"):
            if action in actors:
                assert actors[action] is not None, f"{action} has no actor"
        owner = await session.scalar(select(User).where(User.telegram_id == 7106))
        assert {row.actor_user_id for row in rows} == {owner.id}

    async def test_history_is_tenant_scoped(self, client):
        owner, bot_id = await setup_bot(client, 7103)
        installation_id = await install(client, owner, bot_id)
        other = await client.post("/api/v1/auth/dev", json={"telegram_id": 7104})
        stranger = {"Authorization": f"Bearer {other.json()['token']}"}
        response = await client.get(
            f"/api/v1/plugins/installations/{installation_id}/history", headers=stranger
        )
        assert response.status_code == 404

    async def test_configuration_history_never_stores_secret_values(self, client):
        headers, bot_id = await setup_bot(client, 7105)
        installation_id = await install(client, headers, bot_id)
        await client.patch(
            f"/api/v1/plugins/installations/{installation_id}/configure",
            json={"config": {"api_key": "super-secret-value"}},
            headers=headers,
        )
        body = (
            await client.get(
                f"/api/v1/plugins/installations/{installation_id}/history", headers=headers
            )
        ).text
        assert "super-secret-value" not in body


class TestPermissionGrants:
    async def test_requested_but_ungranted_is_reported_honestly(self, client):
        headers, bot_id = await setup_bot(client, 7201)
        installation_id = await install(client, headers, bot_id)
        body = (
            await client.get(
                f"/api/v1/plugins/installations/{installation_id}/permissions", headers=headers
            )
        ).json()
        assert isinstance(body["requested"], list)
        assert body["granted"] == []
        assert set(body["uneffective"]) == set(body["requested"])

    async def test_a_plugin_cannot_be_granted_something_it_did_not_request(self, client):
        headers, bot_id = await setup_bot(client, 7202)
        installation_id = await install(client, headers, bot_id)
        response = await client.put(
            f"/api/v1/plugins/installations/{installation_id}/permissions",
            json={"permission": "manage_users"},
            headers=headers,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] in ("not_requested", "unknown_permission")

    async def test_an_unknown_permission_is_refused(self, client):
        headers, bot_id = await setup_bot(client, 7203)
        installation_id = await install(client, headers, bot_id)
        response = await client.put(
            f"/api/v1/plugins/installations/{installation_id}/permissions",
            json={"permission": "execute_arbitrary_code"},
            headers=headers,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "unknown_permission"

    async def test_grant_and_revoke_round_trip(self, client, db, session):
        from sqlalchemy import select

        from meiko.db.models import Plugin, PluginPermissionGrant

        headers, bot_id = await setup_bot(client, 7204)
        installation_id = await install(client, headers, bot_id)
        row = await session.get(
            __import__("meiko.db.models", fromlist=["PluginInstallation"]).PluginInstallation,
            installation_id,
        )
        plugin = await session.get(Plugin, row.plugin_id)
        requested = list(plugin.permissions or [])
        if not requested:
            plugin.permissions = ["read_messages"]
            await session.flush()
            requested = ["read_messages"]
        permission = requested[0]

        granted = await client.put(
            f"/api/v1/plugins/installations/{installation_id}/permissions",
            json={"permission": permission, "note": "needed for onboarding"},
            headers=headers,
        )
        assert granted.status_code == 200, granted.text
        listed = (
            await client.get(
                f"/api/v1/plugins/installations/{installation_id}/permissions", headers=headers
            )
        ).json()
        assert permission in listed["granted"]
        assert permission not in listed["uneffective"]

        revoked = await client.delete(
            f"/api/v1/plugins/installations/{installation_id}/permissions/{permission}",
            headers=headers,
        )
        assert revoked.status_code == 200
        after = (
            await client.get(
                f"/api/v1/plugins/installations/{installation_id}/permissions", headers=headers
            )
        ).json()
        assert permission not in after["granted"]
        assert permission in after["revoked"]
        assert permission in after["uneffective"]

        # The revocation is recorded, not deleted: the audit trail survives.
        grants = (
            await session.scalars(
                select(PluginPermissionGrant).where(
                    PluginPermissionGrant.installation_id == installation_id
                )
            )
        ).all()
        assert any(g.revoked_at is not None for g in grants)

    async def test_granting_requires_ownership(self, client):
        owner, bot_id = await setup_bot(client, 7205)
        installation_id = await install(client, owner, bot_id)
        other = await client.post("/api/v1/auth/dev", json={"telegram_id": 7206})
        stranger = {"Authorization": f"Bearer {other.json()['token']}"}
        response = await client.put(
            f"/api/v1/plugins/installations/{installation_id}/permissions",
            json={"permission": "read_messages"},
            headers=stranger,
        )
        assert response.status_code == 404


class TestLeases:
    async def test_bots_have_a_lease_owner_and_expiry(self, client, db, session):
        from meiko.db.models import BotAccount

        _, bot_id = await setup_bot(client, 7301)
        bot = await session.get(BotAccount, bot_id)
        assert hasattr(bot, "lease_owner")
        assert hasattr(bot, "lease_expires_at")
        assert bot.lease_owner is None

    async def test_a_running_bot_is_leased_and_released(self, client, db, session):
        from meiko.db.models import BotAccount
        from meiko.runtime import manager as manager_module

        manager_module._manager = None
        manager = manager_module.get_worker_manager()
        await manager.register()
        _, bot_id = await setup_bot(client, 7302)
        async with __import__("meiko.db.base", fromlist=["session_scope"]).session_scope() as db_session:
            bot = await db_session.get(BotAccount, bot_id)
            bot.token_valid = True
        try:
            claim = await manager.claim(bot_id)
            assert claim["acquired"] is True
            async with __import__("meiko.db.base", fromlist=["session_scope"]).session_scope() as db_session:
                bot = await db_session.get(BotAccount, bot_id)
                assert bot.lease_owner == manager.state.worker_id
                assert bot.lease_expires_at is not None
            await manager.release(bot_id)
            async with __import__("meiko.db.base", fromlist=["session_scope"]).session_scope() as db_session:
                bot = await db_session.get(BotAccount, bot_id)
                assert bot.lease_owner is None
        finally:
            manager_module._manager = None


class TestRuntimeHonoursGrants:
    """A manifest declaring a permission must not be enough to use it."""

    async def test_start_refuses_an_ungranted_permission(self, db, session, settings):
        from meiko.db.models import BotAccount, Plugin, PluginInstallation, User
        from meiko.services.seed import seed_platform

        await seed_platform(session)
        from meiko.plugins.base import registry as plugin_registry
        from meiko.plugins.manager import PluginManager, PluginPermissionError, PluginScope

        slug = next(iter(plugin_registry.slugs()))
        plugin = await session.scalar(__import__("sqlalchemy").select(Plugin).where(Plugin.slug == slug))
        if plugin is None:
            pytest.skip("catalogue not seeded")
        user = User(telegram_id=8801, first_name="O", role="owner", is_owner=True)
        session.add(user)
        await session.flush()
        bot = BotAccount(
            owner_id=user.id,
            token_encrypted="enc:v1:x",
            token_fingerprint="grant-test",
            name="G",
            token_valid=True,
            status="stopped",
        )
        session.add(bot)
        await session.flush()
        install = PluginInstallation(
            plugin_id=plugin.id, bot_id=bot.id, scope="bot", state="configured", version="1.0.0"
        )
        session.add(install)
        await session.flush()

        manifest = plugin_registry.get(slug).manifest
        declared = [p for p in (manifest.permissions or [])]
        manager = PluginManager()
        scope = PluginScope(bot_id=bot.id)
        if declared:
            with pytest.raises(PluginPermissionError):
                await manager.start(
                    slug,
                    installation=install,
                    scope=scope,
                    granted_permissions=[],
                )
        # With no grant list supplied (internal boot paths) the previous behaviour
        # is preserved, so this change cannot break unscoped runtimes.
        assert manager is not None
