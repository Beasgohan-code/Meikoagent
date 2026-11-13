"""Plugin authorization: three levels, no global-scope hole, no ID guessing.

The brief's PART 2 is explicit about *who* may do *what*:

* a **platform owner** may touch global installations, approve/block packages and
  manage the marketplace;
* a **bot owner** may act on installations of bots they own – never on global
  rows and never on another tenant's bot;
* a **group admin** may only change group-scoped settings, and only when the bot
  owner allowed it.

These tests are the executable form of that paragraph.  They deliberately use
only the public HTTP surface so an implementation detail cannot quietly reopen
the hole: the earlier bug was that ``POST /plugins/{slug}/install`` without a
``bot_id`` produced a *global* row for anybody who was logged in.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

VALID_TOKEN = "123456789:" + "Z" * 34
SECOND_TOKEN = "987654321:" + "Y" * 34


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


async def login(client: AsyncClient, telegram_id: int) -> tuple[str, dict]:
    response = await client.post(
        "/api/v1/auth/dev",
        json={"telegram_id": telegram_id, "username": f"user{telegram_id}", "first_name": "T"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["token"], payload["user"]


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def make_owner(client: AsyncClient, telegram_id: int = 4242) -> str:
    """Promote a user to platform owner through the database, like the seed does."""
    from sqlalchemy import select

    from meiko.db.base import session_scope
    from meiko.db.models import User

    token, _ = await login(client, telegram_id)
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        assert user is not None
        user.is_owner = True
        user.role = "owner"
    return token


async def new_bot(client: AsyncClient, token: str, bot_token: str) -> int:
    response = await client.post(
        "/api/v1/bots", json={"token": bot_token, "name": "Bot"}, headers=headers(token)
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


class TestOrdinaryUserCannotReachGlobalScope:
    """The reported flaw: no ``bot_id`` used to mean "global, no questions asked"."""

    async def test_install_without_bot_id_is_refused(self, client):
        token, _ = await login(client, 3001)
        response = await client.post(
            "/api/v1/plugins/welcome/install", json={}, headers=headers(token)
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "global_scope_forbidden"

    async def test_install_with_explicit_null_bot_id_is_refused(self, client):
        token, _ = await login(client, 3002)
        response = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": None}, headers=headers(token)
        )
        assert response.status_code == 403

    async def test_install_and_enable_roundtrip_writes_nothing_global(self, client):
        from sqlalchemy import select

        from meiko.db.base import session_scope
        from meiko.db.models import PluginInstallation

        token, _ = await login(client, 3003)
        await client.post("/api/v1/plugins/welcome/install", json={}, headers=headers(token))
        async with session_scope() as session:
            rows = (
                await session.scalars(
                    select(PluginInstallation).where(PluginInstallation.bot_id.is_(None))
                )
            ).all()
        assert rows == []

    async def test_toggle_without_bot_id_is_refused(self, client):
        token, _ = await login(client, 3004)
        response = await client.post(
            "/api/v1/plugins/welcome/toggle", headers=headers(token), params={"enabled": "true"}
        )
        assert response.status_code == 403

    async def test_group_scope_install_requires_owned_bot(self, client):
        """A group id alone cannot be borrowed: the owning bot must be named."""
        from sqlalchemy import select

        from meiko.db.base import session_scope
        from meiko.db.models import PluginInstallation

        token, _ = await login(client, 3005)
        response = await client.post(
            "/api/v1/plugins/welcome/install",
            json={"group_id": 1},
            headers=headers(token),
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "bot_required"
        async with session_scope() as session:
            rows = (await session.scalars(select(PluginInstallation))).all()
        assert rows == []

    async def test_owner_global_install_is_allowed_and_audited(self, client):
        from sqlalchemy import select

        from meiko.db.base import session_scope
        from meiko.db.models import AuditLog, PluginInstallation

        owner = await make_owner(client, 3100)
        response = await client.post(
            "/api/v1/plugins/welcome/install", json={}, headers=headers(owner)
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["scope"] == "global"
        async with session_scope() as session:
            actions = {
                row.action
                for row in (await session.scalars(select(AuditLog))).all()
            }
            install = await session.get(PluginInstallation, payload["id"])
        assert install is not None and install.bot_id is None
        assert "plugin.global_installed" in actions

    async def test_global_install_rejected_when_owner_disables_it(self, client, monkeypatch):
        from meiko.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "plugin_global_scope_enabled", False, raising=False)
        owner = await make_owner(client, 3101)
        response = await client.post(
            "/api/v1/plugins/welcome/install", json={}, headers=headers(owner)
        )
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "global_scope_disabled"


class TestGlobalRowsAreOwnerOnly:
    async def test_owner_creates_global_row_then_stranger_gets_only_404(self, client):
        owner = await make_owner(client, 3200)
        created = await client.post(
            "/api/v1/plugins/welcome/install", json={}, headers=headers(owner)
        )
        installation_id = created.json()["id"]
        stranger, _ = await login(client, 3201)

        # Every installation-scoped route must look *identical* to a missing row:
        # no 403 that would confirm the id exists in someone else's tenant.
        for method, path in (
            ("patch", f"/api/v1/plugins/installations/{installation_id}/configure"),
            ("post", f"/api/v1/plugins/installations/{installation_id}/enable"),
            ("post", f"/api/v1/plugins/installations/{installation_id}/disable"),
            ("post", f"/api/v1/plugins/installations/{installation_id}/restart"),
            ("delete", f"/api/v1/plugins/installations/{installation_id}"),
            ("get", f"/api/v1/plugins/installations/{installation_id}/logs"),
        ):
            kwargs = {"headers": headers(stranger)}
            if method == "patch":
                kwargs["json"] = {"config": {"welcome_text": "x"}}
            response = await getattr(client, method)(path, **kwargs)
            assert response.status_code == 404, (method, path, response.text)
            assert response.json()["detail"]["error"] == "not_found"

    async def test_stranger_cannot_read_global_logs(self, client):
        owner = await make_owner(client, 3202)
        created = await client.post(
            "/api/v1/plugins/welcome/install", json={}, headers=headers(owner)
        )
        installation_id = created.json()["id"]
        stranger, _ = await login(client, 3203)
        response = await client.get(
            f"/api/v1/plugins/installations/{installation_id}/logs", headers=headers(stranger)
        )
        assert response.status_code == 404

    async def test_owner_can_configure_and_disable_a_global_row(self, client):
        owner = await make_owner(client, 3204)
        created = await client.post(
            "/api/v1/plugins/welcome/install", json={}, headers=headers(owner)
        )
        installation_id = created.json()["id"]
        configured = await client.patch(
            f"/api/v1/plugins/installations/{installation_id}/configure",
            json={"config": {"welcome_text": "hi {user}"}},
            headers=headers(owner),
        )
        assert configured.status_code == 200, configured.text
        enabled = await client.post(
            f"/api/v1/plugins/installations/{installation_id}/enable", headers=headers(owner)
        )
        assert enabled.status_code == 200
        disabled = await client.post(
            f"/api/v1/plugins/installations/{installation_id}/disable", headers=headers(owner)
        )
        assert disabled.status_code == 200
        assert disabled.json()["state"] == "disabled"

    async def test_global_row_not_listed_for_ordinary_users(self, client):
        owner = await make_owner(client, 3205)
        await client.post("/api/v1/plugins/welcome/install", json={}, headers=headers(owner))
        stranger, _ = await login(client, 3206)
        listing = await client.get("/api/v1/plugins/installed", headers=headers(stranger))
        assert listing.status_code == 200
        assert listing.json() == []


class TestBotOwnerBoundaries:
    async def test_own_bot_install_is_allowed(self, client):
        token, _ = await login(client, 3300)
        bot_id = await new_bot(client, token, VALID_TOKEN)
        response = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=headers(token)
        )
        assert response.status_code == 201
        assert response.json()["scope"] == "bot"

    async def test_other_tenants_installation_is_invisible(self, client):
        alice, _ = await login(client, 3301)
        bob, _ = await login(client, 3302)
        bot_id = await new_bot(client, alice, VALID_TOKEN)
        created = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=headers(alice)
        )
        installation_id = created.json()["id"]
        response = await client.post(
            f"/api/v1/plugins/installations/{installation_id}/disable", headers=headers(bob)
        )
        assert response.status_code == 404

    async def test_stranger_bot_id_cannot_be_used(self, client):
        alice, _ = await login(client, 3303)
        bob, _ = await login(client, 3304)
        bot_id = await new_bot(client, alice, VALID_TOKEN)
        response = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=headers(bob)
        )
        assert response.status_code == 404

    async def test_stranger_cannot_list_another_bots_installations(self, client):
        alice, _ = await login(client, 3305)
        bob, _ = await login(client, 3306)
        bot_id = await new_bot(client, alice, VALID_TOKEN)
        for path in ("/plugins/installed", "/plugins/toggles"):
            response = await client.get(
                f"{path}?bot_id={bot_id}", headers=headers(bob)
            )
            assert response.status_code == 404, path

    async def test_owner_may_manage_any_bots_installation(self, client):
        alice, _ = await login(client, 3307)
        bot_id = await new_bot(client, alice, VALID_TOKEN)
        created = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=headers(alice)
        )
        installation_id = created.json()["id"]
        owner = await make_owner(client, 3308)
        response = await client.post(
            f"/api/v1/plugins/installations/{installation_id}/disable", headers=headers(owner)
        )
        assert response.status_code == 200


class TestGroupScopeAuthority:
    async def _group(self, client, token: str, bot_id: int, chat_id: int) -> int:
        """Register a group the way the bot does, through the service layer."""
        from meiko.db.base import session_scope
        from meiko.services.core import GroupService

        async with session_scope() as session:
            group = await GroupService(session).upsert(
                bot_id, chat_id, title="G", chat_type="supergroup"
            )
            return group.id

    async def test_bot_owner_may_install_for_their_group(self, client):
        token, _ = await login(client, 3400)
        bot_id = await new_bot(client, token, VALID_TOKEN)
        group_id = await self._group(client, token, bot_id, -1001)
        response = await client.post(
            "/api/v1/plugins/welcome/install",
            json={"bot_id": bot_id, "group_id": group_id},
            headers=headers(token),
        )
        assert response.status_code == 201, response.text
        assert response.json()["scope"] == "group"

    async def test_group_row_from_another_bot_is_invisible(self, client):
        alice, _ = await login(client, 3401)
        bob, _ = await login(client, 3402)
        alice_bot = await new_bot(client, alice, VALID_TOKEN)
        group_id = await self._group(client, alice, alice_bot, -1001)
        bob_bot = await new_bot(client, bob, SECOND_TOKEN)
        response = await client.post(
            "/api/v1/plugins/welcome/install",
            json={"bot_id": bob_bot, "group_id": group_id},
            headers=headers(bob),
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "not_found"

    async def test_group_settings_patch_requires_bot_ownership(self, client):
        alice, _ = await login(client, 3403)
        bob, _ = await login(client, 3404)
        alice_bot = await new_bot(client, alice, VALID_TOKEN)
        group_id = await self._group(client, alice, alice_bot, -1001)
        response = await client.patch(
            f"/api/v1/groups/{group_id}/settings",
            json={"welcome_enabled": False},
            headers=headers(bob),
        )
        assert response.status_code == 404


class TestEffectiveStateIsRealRuntimeState:
    """PART 2.4: ``/plugins/effective`` must report the runtime, not a guess."""

    async def test_running_comes_from_the_plugin_manager(self, client, monkeypatch):
        from meiko.plugins.context import PluginScope
        from meiko.plugins.manager import PluginManager
        from meiko.runtime.manager import get_worker_manager

        token, _ = await login(client, 3500)
        bot_id = await new_bot(client, token, VALID_TOKEN)
        created = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=headers(token)
        )
        installation_id = created.json()["id"]
        enabled = await client.post(
            f"/api/v1/plugins/installations/{installation_id}/enable", headers=headers(token)
        )
        assert enabled.status_code == 200

        before = (
            await client.get("/api/v1/plugins/effective", headers=headers(token))
        ).json()
        row = before["bots"][str(bot_id)]["plugins"]["welcome"]
        # The bot is NOT running, so the plugin cannot be running either.
        assert row["running"] is False
        assert row["status"] in ("failed", "stopped")

        # Now fake a running bot application with a plugin manager that says
        # "welcome is running".  The endpoint must believe the manager.
        from meiko.plugins.manager import RunningPlugin

        class FakeApplication:
            def __init__(self) -> None:
                self.plugins = PluginManager()
                class FakeContext:
                    jobs: list = []
                    subscriptions: list = []

                self.plugins._running[
                    self.plugins.key("welcome", PluginScope(bot_id=bot_id))
                ] = RunningPlugin(
                    slug="welcome",
                    scope=PluginScope(bot_id=bot_id),
                    instance=None,  # type: ignore[arg-type]
                    context=FakeContext(),  # type: ignore[arg-type]
                )

        manager = get_worker_manager()
        monkeypatch.setitem(manager.state.applications, bot_id, FakeApplication())

        after = (await client.get("/api/v1/plugins/effective", headers=headers(token))).json()
        row = after["bots"][str(bot_id)]["plugins"]["welcome"]
        assert row["running"] is True
        assert row["status"] == "running"

    async def test_group_scope_rows_are_included(self, client):
        from meiko.db.base import session_scope
        from meiko.services.core import GroupService

        token, _ = await login(client, 3501)
        bot_id = await new_bot(client, token, VALID_TOKEN)
        async with session_scope() as session:
            group = await GroupService(session).upsert(
                bot_id, -1005, title="Lounge", chat_type="supergroup"
            )
            group_id = group.id
        created = await client.post(
            "/api/v1/plugins/welcome/install",
            json={"bot_id": bot_id, "group_id": group_id},
            headers=headers(token),
        )
        assert created.status_code == 201, created.text
        enabled = await client.post(
            f"/api/v1/plugins/installations/{created.json()['id']}/enable",
            headers=headers(token),
        )
        assert enabled.status_code == 200
        body = (await client.get("/api/v1/plugins/effective", headers=headers(token))).json()
        row = body["bots"][str(bot_id)]["plugins"]["welcome"]
        assert row["scope"] == "group"
        assert [g["group_id"] for g in row["group_overrides"]] == [group_id]

    async def test_missing_configuration_is_reported_as_such(self, client):
        token, _ = await login(client, 3502)
        bot_id = await new_bot(client, token, VALID_TOKEN)
        created = await client.post(
            "/api/v1/plugins/ai/install", json={"bot_id": bot_id}, headers=headers(token)
        )
        body = (await client.get("/api/v1/plugins/effective", headers=headers(token))).json()
        row = body["bots"][str(bot_id)]["plugins"]["ai"]
        assert row["missing_config"], "ai plugin needs an api_key"
        assert row["status"] == "installed"
        assert set(body["statuses"]) >= {
            "available", "installed", "approved", "enabled", "configured",
            "running", "stopped", "failed", "blocked", "missing_dependency",
            "missing_permission", "missing_configuration",
        }

    async def test_disabled_plugin_reports_stopped_not_running(self, client):
        token, _ = await login(client, 3503)
        bot_id = await new_bot(client, token, VALID_TOKEN)
        created = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=headers(token)
        )
        installation_id = created.json()["id"]
        await client.post(
            f"/api/v1/plugins/installations/{installation_id}/enable", headers=headers(token)
        )
        await client.post(
            f"/api/v1/plugins/installations/{installation_id}/disable", headers=headers(token)
        )
        body = (await client.get("/api/v1/plugins/effective", headers=headers(token))).json()
        row = body["bots"][str(bot_id)]["plugins"]["welcome"]
        assert row["running"] is False
        assert row["status"] == "stopped"
        assert row["enabled"] is False


class TestOwnerGlobalSurface:
    """Global scope has its own owner-only surface; ordinary APIs never expose it."""

    async def test_global_endpoints_are_owner_only(self, client):
        token, _ = await login(client, 3600)
        for path in (
            "/api/v1/owner/plugins/global",
            "/api/v1/owner/plugins/overview",
        ):
            response = await client.get(path, headers=headers(token))
            assert response.status_code == 403, path
            assert response.json()["detail"]["error"] == "forbidden"

    async def test_global_list_reports_live_state(self, client):
        owner = await make_owner(client, 3601)
        await client.post("/api/v1/plugins/welcome/install", json={}, headers=headers(owner))
        body = (await client.get("/api/v1/owner/plugins/global", headers=headers(owner))).json()
        assert len(body) == 1
        assert body[0]["slug"] == "welcome"
        assert body[0]["scope"] == "global"
        assert body[0]["running_somewhere"] is False
        assert body[0]["status"] in ("stopped", "installed", "failed")

    async def test_reconcile_reports_no_running_bots_honestly(self, client):
        owner = await make_owner(client, 3602)
        await client.post("/api/v1/plugins/welcome/install", json={}, headers=headers(owner))
        response = await client.post(
            "/api/v1/owner/plugins/global/welcome/reconcile", headers=headers(owner)
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["bots"] == {}
        assert body["applied"] == 0

    async def test_plugin_overview_counts_installations(self, client):
        owner = await make_owner(client, 3603)
        await client.post("/api/v1/plugins/welcome/install", json={}, headers=headers(owner))
        body = (await client.get("/api/v1/owner/plugins/overview", headers=headers(owner))).json()
        welcome = next(row for row in body["plugins"] if row["slug"] == "welcome")
        assert welcome["installations"]["global"] == 1
        assert body["totals"]["global_installations"] == 1
        assert body["totals"]["catalogue"] >= 20

    async def test_owner_install_is_audited_with_actor_role(self, client):
        from sqlalchemy import select

        from meiko.db.base import session_scope
        from meiko.db.models import AuditLog

        owner = await make_owner(client, 3604)
        await client.post("/api/v1/plugins/welcome/install", json={}, headers=headers(owner))
        async with session_scope() as session:
            row = await session.scalar(
                select(AuditLog).where(AuditLog.action == "plugin.global_installed")
            )
        assert row is not None
        assert row.metadata_json["actor_role"] == "owner"
        assert row.metadata_json["scope"] == "global"
