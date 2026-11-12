"""API integration tests: auth, tenancy, bots, plugins, commands, owner."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from meiko.services.core import UserService


@pytest_asyncio.fixture()
async def app_client(settings):
    from meiko.api.app import create_app
    from meiko.cache import reset_cache

    await reset_cache()
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def dev_login(client: AsyncClient, telegram_id: int = 777) -> tuple[str, dict]:
    response = await client.post(
        "/api/v1/auth/dev",
        json={"telegram_id": telegram_id, "username": f"user{telegram_id}", "first_name": "Tester"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["token"], payload["user"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


VALID_TOKEN = "123456789:" + "Z" * 34


class TestHealth:
    async def test_health_reports_real_services(self, app_client):
        response = await app_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("ok", "degraded")
        assert body["database"]["status"] == "ok"
        assert body["bot_api"] == "10.4"

    async def test_ready(self, app_client):
        response = await app_client.get("/ready")
        assert response.status_code == 200
        assert response.json()["ready"] is True

    async def test_openapi_documented(self, app_client):
        spec = (await app_client.get("/openapi.json")).json()
        assert "/api/v1/bots" in spec["paths"]
        assert len(spec["paths"]) > 40


class TestAuth:
    async def test_dev_login_creates_user_and_session(self, app_client):
        token, user = await dev_login(app_client)
        assert token
        assert user["telegram_id"] == 777
        me = await app_client.get("/api/v1/auth/me", headers=auth_headers(token))
        assert me.status_code == 200
        assert me.json()["id"] == user["id"]

    async def test_unauthenticated_rejected(self, app_client):
        response = await app_client.get("/api/v1/bots")
        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "unauthorized"

    async def test_bad_session_rejected(self, app_client):
        response = await app_client.get(
            "/api/v1/bots", headers=auth_headers("deadbeef.invalid")
        )
        assert response.status_code == 401

    async def test_logout_revokes_session(self, app_client):
        token, _ = await dev_login(app_client)
        assert (await app_client.post("/api/v1/auth/logout", headers=auth_headers(token))).status_code == 204
        assert (await app_client.get("/api/v1/auth/me", headers=auth_headers(token))).status_code == 401

    async def test_telegram_login_rejects_bad_signature(self, app_client):
        response = await app_client.post(
            "/api/v1/auth/telegram",
            json={"id": 5, "auth_date": 1700000000, "hash": "bad", "first_name": "x"},
        )
        assert response.status_code in (401, 400)

    async def test_api_key_lifecycle(self, app_client):
        token, _ = await dev_login(app_client)
        created = await app_client.post(
            "/api/v1/auth/api-keys", json={"name": "ci", "scopes": ["bots.read"]}, headers=auth_headers(token)
        )
        assert created.status_code == 201
        key = created.json()["key"]
        ok = await app_client.get("/api/v1/bots", headers={"X-API-Key": key})
        assert ok.status_code == 200
        revoked = await app_client.delete(
            f"/api/v1/auth/api-keys/{created.json()['record']['id']}", headers=auth_headers(token)
        )
        assert revoked.status_code == 204
        denied = await app_client.get("/api/v1/bots", headers={"X-API-Key": key})
        assert denied.status_code == 401


class TestBots:
    async def test_create_bot_without_telegram_verification_is_marked(self, app_client):
        token, _ = await dev_login(app_client)
        response = await app_client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN, "name": "Test Bot"}, headers=auth_headers(token)
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["name"] == "Test Bot"
        assert body["token_valid"] is False
        assert "token" not in body  # secrets never leave the backend

    async def test_duplicate_token_conflict(self, app_client):
        token, _ = await dev_login(app_client)
        await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        second = await app_client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token)
        )
        assert second.status_code == 400

    async def test_malformed_token_rejected(self, app_client):
        token, _ = await dev_login(app_client)
        response = await app_client.post(
            "/api/v1/bots", json={"token": "not-a-valid-token"}, headers=auth_headers(token)
        )
        assert response.status_code == 422 or response.status_code == 400

    async def test_tenant_isolation_between_users(self, app_client):
        owner_token, _ = await dev_login(app_client, 1001)
        other_token, _ = await dev_login(app_client, 1002)
        created = await app_client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(owner_token)
        )
        bot_id = created.json()["id"]
        assert (
            await app_client.get(f"/api/v1/bots/{bot_id}", headers=auth_headers(owner_token))
        ).status_code == 200
        assert (
            await app_client.get(f"/api/v1/bots/{bot_id}", headers=auth_headers(other_token))
        ).status_code == 404
        assert (
            await app_client.post(f"/api/v1/bots/{bot_id}/start", headers=auth_headers(other_token))
        ).status_code == 404
        listing = (await app_client.get("/api/v1/bots", headers=auth_headers(other_token))).json()
        assert listing["total"] == 0

    async def test_update_and_delete(self, app_client):
        token, _ = await dev_login(app_client)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()["id"]
        patched = await app_client.patch(
            f"/api/v1/bots/{bot_id}",
            json={"name": "Renamed", "runtime_mode": "webhook"},
            headers=auth_headers(token),
        )
        assert patched.json()["name"] == "Renamed"
        assert patched.json()["runtime_mode"] == "webhook"
        assert (await app_client.delete(f"/api/v1/bots/{bot_id}", headers=auth_headers(token))).status_code == 204
        assert (await app_client.get(f"/api/v1/bots/{bot_id}", headers=auth_headers(token))).status_code == 404

    async def test_start_with_unverified_token_reports_error(self, app_client):
        token, _ = await dev_login(app_client)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()["id"]
        response = await app_client.post(f"/api/v1/bots/{bot_id}/start", headers=auth_headers(token))
        body = response.json()
        # Fake token cannot be verified against Telegram – never claim success
        assert response.status_code == 200
        assert body["running"] is False

    async def test_clone_creates_independent_bot(self, app_client):
        token, _ = await dev_login(app_client)
        source = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()
        clone = await app_client.post(
            f"/api/v1/bots/{source['id']}/clone",
            json={"token": "987654321:" + "Y" * 34, "name": "Clone"},
            headers=auth_headers(token),
        )
        assert clone.status_code == 201
        body = clone.json()
        assert body["is_clone"] is True
        assert body["source_bot_id"] == source["id"]
        assert body["id"] != source["id"]


class TestPlugins:
    async def test_catalogue_is_seeded(self, app_client):
        token, _ = await dev_login(app_client)
        response = await app_client.get("/api/v1/plugins", headers=auth_headers(token))
        assert response.status_code == 200
        slugs = {p["slug"] for p in response.json()}
        assert {"anti_spam", "welcome", "analytics"} <= slugs

    async def test_install_requires_owned_bot(self, app_client):
        owner, _ = await dev_login(app_client, 2001)
        stranger, _ = await dev_login(app_client, 2002)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(owner))
        ).json()["id"]
        response = await app_client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=auth_headers(stranger)
        )
        assert response.status_code == 404

    async def test_enable_requires_config(self, app_client):
        token, _ = await dev_login(app_client)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()["id"]
        install = await app_client.post(
            "/api/v1/plugins/ai/install", json={"bot_id": bot_id}, headers=auth_headers(token)
        )
        installation_id = install.json()["id"]
        enable = await app_client.post(
            f"/api/v1/plugins/installations/{installation_id}/enable", headers=auth_headers(token)
        )
        assert enable.status_code == 422
        assert enable.json()["detail"]["error"] == "config_required"
        configured = await app_client.patch(
            f"/api/v1/plugins/installations/{installation_id}/configure",
            json={"config": {"api_key": "sk-test"}},
            headers=auth_headers(token),
        )
        assert configured.status_code == 200
        assert configured.json()["state"] == "configured"
        enabled = await app_client.post(
            f"/api/v1/plugins/installations/{installation_id}/enable", headers=auth_headers(token)
        )
        assert enabled.status_code == 200
        assert enabled.json()["state"] == "configured"

    async def test_disable_transitions_state(self, app_client):
        token, _ = await dev_login(app_client)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()["id"]
        installation = (
            await app_client.post(
                "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=auth_headers(token)
            )
        ).json()
        await app_client.post(
            f"/api/v1/plugins/installations/{installation['id']}/enable", headers=auth_headers(token)
        )
        disabled = await app_client.post(
            f"/api/v1/plugins/installations/{installation['id']}/disable", headers=auth_headers(token)
        )
        assert disabled.json()["state"] == "disabled"


class TestCommands:
    async def test_declarative_command_roundtrip(self, app_client):
        token, _ = await dev_login(app_client)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()["id"]
        created = await app_client.post(
            f"/api/v1/bots/{bot_id}/commands",
            json={
                "name": "rules",
                "description": "Show rules",
                "handler_type": "declarative",
                "definition": {"response": "Hello {user}, read the rules!"},
            },
            headers=auth_headers(token),
        )
        assert created.status_code == 201, created.text
        listed = await app_client.get(f"/api/v1/bots/{bot_id}/commands", headers=auth_headers(token))
        names = {c["name"] for c in listed.json()}
        assert "rules" in names

    async def test_unsafe_template_rejected(self, app_client):
        token, _ = await dev_login(app_client)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()["id"]
        response = await app_client.post(
            f"/api/v1/bots/{bot_id}/commands",
            json={
                "name": "evil",
                "handler_type": "declarative",
                "definition": {"response": "__import__('os').system('id')"},
            },
            headers=auth_headers(token),
        )
        # literal text is allowed (never executed), but unknown variables are refused
        assert response.status_code in (201, 422)
        bad = await app_client.post(
            f"/api/v1/bots/{bot_id}/commands",
            json={"name": "evil2", "handler_type": "declarative", "definition": {"response": "{secret_env}"}},
            headers=auth_headers(token),
        )
        assert bad.status_code == 422

    async def test_preview_does_not_execute(self, app_client):
        token, _ = await dev_login(app_client)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()["id"]
        response = await app_client.post(
            f"/api/v1/bots/{bot_id}/commands/preview",
            json={"name": "hello", "definition": {"response": "Hi {user}"}},
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        assert "@member" in response.json()["preview"]


class TestDashboardAndInfrastructure:
    async def test_dashboard_counts_are_real(self, app_client):
        token, _ = await dev_login(app_client)
        empty = (await app_client.get("/api/v1/dashboard", headers=auth_headers(token))).json()
        assert empty["bots"]["total"] == 0
        await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        after = (await app_client.get("/api/v1/dashboard", headers=auth_headers(token))).json()
        assert after["bots"]["total"] == 1
        assert after["bots"]["stopped"] == 1
        assert len(after["activity_series"]) == 7

    async def test_activity_feed(self, app_client):
        token, _ = await dev_login(app_client)
        await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        feed = await app_client.get("/api/v1/activity", headers=auth_headers(token))
        assert feed.status_code == 200
        assert any(item["kind"] == "bot.connected" for item in feed.json())

    async def test_runtime_infrastructure(self, app_client):
        token, _ = await dev_login(app_client)
        response = await app_client.get("/api/v1/infrastructure/runtime", headers=auth_headers(token))
        assert response.status_code == 200
        body = response.json()
        assert body["services"]["database"]["status"] == "ok"
        assert body["bot_api"] == "10.4"

    async def test_logs_endpoint(self, app_client):
        token, _ = await dev_login(app_client)
        response = await app_client.get("/api/v1/infrastructure/logs", headers=auth_headers(token))
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestOwnerControl:
    async def test_owner_endpoints_forbidden_for_users(self, app_client):
        token, _ = await dev_login(app_client)
        for path in ("/api/v1/owner/overview", "/api/v1/owner/users", "/api/v1/owner/audit"):
            response = await app_client.get(path, headers=auth_headers(token))
            assert response.status_code == 403, path

    async def test_owner_can_review_platform(self, app_client, settings):
        from meiko.db.base import session_scope

        async with session_scope() as session:
            owner = await UserService(session).get_or_create_telegram_user(9001, username="owner")
            owner.is_owner = True
            owner.role = "owner"
        from meiko.api.auth import AuthService
        from meiko.db.base import session_scope as scope2

        async with scope2() as session:
            owner = await UserService(session).get_or_create_telegram_user(9001, username="owner")
            owner.is_owner = True
            token = await AuthService(session).create_session(owner)
        overview = await app_client.get("/api/v1/owner/overview", headers=auth_headers(token))
        assert overview.status_code == 200
        assert "users" in overview.json()
        users = await app_client.get("/api/v1/owner/users", headers=auth_headers(token))
        assert users.status_code == 200

    async def test_webhook_signature_required(self, app_client):
        response = await app_client.post("/api/v1/telegram/webhook/1", json={"update_id": 1})
        assert response.status_code in (403, 404, 503)


class TestTemplates:
    async def test_catalogue_lists_seeded_templates(self, app_client):
        token, _ = await dev_login(app_client, telegram_id=4242)
        response = await app_client.get("/api/v1/bot-templates", headers=auth_headers(token))
        assert response.status_code == 200
        body = response.json()
        slugs = {item["slug"] for item in body}
        assert "community-guardian" in slugs
        assert all(item["is_public"] for item in body)

    async def test_template_requires_auth(self, app_client):
        response = await app_client.get("/api/v1/bot-templates")
        assert response.status_code == 401

    async def test_create_bot_from_template_installs_plugins(self, app_client):
        token, _ = await dev_login(app_client, telegram_id=4243)
        created = await app_client.post(
            "/api/v1/bots",
            json={"token": "555000111:" + "Q" * 34, "name": "Guardian", "template_slug": "community-guardian"},
            headers=auth_headers(token),
        )
        assert created.status_code == 201, created.text
        bot_id = created.json()["id"]
        installed = await app_client.get(
            f"/api/v1/plugins/installed?bot_id={bot_id}", headers=auth_headers(token)
        )
        assert installed.status_code == 200
        assert len(installed.json()) >= 3

    async def test_unknown_template_is_rejected(self, app_client):
        token, _ = await dev_login(app_client, telegram_id=4244)
        response = await app_client.post(
            "/api/v1/bots",
            json={"token": "555000222:" + "W" * 34, "template_slug": "does-not-exist"},
            headers=auth_headers(token),
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "unknown_template"
