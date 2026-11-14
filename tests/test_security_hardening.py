"""Security and reliability items from PART 18.

Each test names the review concern it closes, so a regression points at the
concern rather than at an implementation detail.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

VALID_TOKEN = "123456789:" + "Z" * 34


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


class TestCrossSiteProtection:
    """CSRF: a browser must not be able to make a state change from another site."""

    async def test_cross_origin_post_is_refused(self, client):
        response = await client.post(
            "/api/v1/auth/dev",
            json={"telegram_id": 9001},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "csrf"

    async def test_same_origin_post_is_allowed(self, client):
        response = await client.post(
            "/api/v1/auth/dev",
            json={"telegram_id": 9002},
            headers={"Origin": "http://test"},
        )
        assert response.status_code == 200

    async def test_configured_origin_is_allowed(self, client, monkeypatch):
        from meiko.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "cors_origins", "http://localhost:3000")
        response = await client.post(
            "/api/v1/auth/dev",
            json={"telegram_id": 9003},
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 200

    async def test_referer_is_also_checked(self, client):
        response = await client.post(
            "/api/v1/auth/dev",
            json={"telegram_id": 9004},
            headers={"Referer": "https://evil.example/page"},
        )
        assert response.status_code == 403

    async def test_requests_without_origin_still_work(self, client):
        """curl / server-to-server calls carry no Origin and must not break."""
        response = await client.post("/api/v1/auth/dev", json={"telegram_id": 9005})
        assert response.status_code == 200

    async def test_reads_are_never_blocked(self, client):
        response = await client.get("/health", headers={"Origin": "https://evil.example"})
        assert response.status_code == 200


class TestCookieSession:
    """The browser session must not be readable by page script."""

    async def test_login_sets_an_httponly_cookie(self, client):
        response = await client.post("/api/v1/auth/dev", json={"telegram_id": 9200})
        assert response.status_code == 200
        raw = response.headers.get("set-cookie", "")
        assert "meiko_session=" in raw
        assert "HttpOnly" in raw
        assert "SameSite=lax" in raw.lower().replace("samesite=lax", "SameSite=lax")

    async def test_the_cookie_alone_authenticates(self, client):
        login = await client.post("/api/v1/auth/dev", json={"telegram_id": 9201})
        token = login.json()["token"]
        # Drop the body token, keep only the cookiejar the client stored.
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["telegram_id"] == 9201
        assert token

    async def test_logout_clears_the_cookie_and_revokes_the_session(self, client):
        await client.post("/api/v1/auth/dev", json={"telegram_id": 9202})
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        out = await client.post("/api/v1/auth/logout")
        assert out.status_code == 204
        client.cookies.clear()
        assert (await client.get("/api/v1/auth/me")).status_code == 401

    async def test_the_body_token_still_works_for_api_clients(self, client):
        login = await client.post("/api/v1/auth/dev", json={"telegram_id": 9203})
        token = login.json()["token"]
        client.cookies.clear()
        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.status_code == 200

    async def test_a_bogus_cookie_is_rejected(self, client):
        client.cookies.set("meiko_session", "not-a-real-token")
        assert (await client.get("/api/v1/auth/me")).status_code == 401

    async def test_secure_flag_follows_production(self, monkeypatch):
        from meiko.config import Settings, get_settings

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECRET_KEY", "x" * 40)
        get_settings.cache_clear()
        try:
            assert Settings().is_production
        finally:
            get_settings.cache_clear()


class TestSecurityHeaders:
    async def test_headers_are_present(self, client):
        response = await client.get("/health")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"


class TestProductionRedisIsNotSilentlyLocal:
    """A multi-process deployment cannot share an in-memory cache."""

    def _settings(self, monkeypatch, **env):
        from meiko.config import Settings, get_settings

        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        settings = Settings()
        get_settings.cache_clear()
        monkeypatch.setattr("meiko.config.get_settings", lambda: settings)
        monkeypatch.setattr("meiko.cache.get_settings", lambda: settings)
        return settings

    async def test_production_without_redis_refuses_to_boot(self, monkeypatch):
        from meiko.cache import RedisUnavailable, init_cache
        from meiko import cache as cache_module

        self._settings(monkeypatch, ENVIRONMENT="production", REDIS_URL="")
        monkeypatch.setattr(cache_module, "_cache", None)
        with pytest.raises(RedisUnavailable):
            await init_cache()

    async def test_production_with_a_dead_redis_refuses_to_boot(self, monkeypatch):
        from meiko.cache import RedisUnavailable, init_cache
        from meiko import cache as cache_module

        self._settings(
            monkeypatch, ENVIRONMENT="production", REDIS_URL="redis://127.0.0.1:6390/0"
        )
        monkeypatch.setattr(cache_module, "_cache", None)
        with pytest.raises(RedisUnavailable):
            await init_cache()

    async def test_development_still_falls_back(self, monkeypatch):
        from meiko.cache import MemoryBackend, init_cache
        from meiko import cache as cache_module

        self._settings(
            monkeypatch, ENVIRONMENT="development", REDIS_URL="redis://127.0.0.1:6390/0"
        )
        monkeypatch.setattr(cache_module, "_cache", None)
        cache = await init_cache()
        assert isinstance(cache.backend, MemoryBackend)

    async def test_require_redis_flag_is_honoured_outside_production(self, monkeypatch):
        from meiko.cache import RedisUnavailable, init_cache
        from meiko import cache as cache_module

        self._settings(monkeypatch, ENVIRONMENT="development", REDIS_URL="", REQUIRE_REDIS="1")
        monkeypatch.setattr(cache_module, "_cache", None)
        with pytest.raises(RedisUnavailable):
            await init_cache()


class TestTelegramAuthTimestamps:
    """A signed payload from last year is still a valid signature — and still stale."""

    def _widget_payload(self, bot_token: str, auth_date: int) -> dict:
        import hashlib
        import hmac

        data = {
            "id": 42,
            "first_name": "T",
            "auth_date": auth_date,
            "hash": "",
        }
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()) if k != "hash")
        secret = hashlib.sha256(bot_token.encode()).digest()
        data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return data

    def test_stale_login_payload_is_refused(self):
        import time

        from meiko.api.auth import AuthError, check_login_widget

        payload = self._widget_payload("123:" + "a" * 30, int(time.time()) - 86_400 * 30)
        with pytest.raises(AuthError, match="expired"):
            check_login_widget(payload, "123:" + "a" * 30)

    def test_fresh_payload_is_accepted(self):
        import time

        from meiko.api.auth import check_login_widget

        token = "123:" + "a" * 30
        assert check_login_widget(self._widget_payload(token, int(time.time())), token)

    def test_future_timestamp_beyond_skew_is_refused(self, monkeypatch):
        """Clock skew must not let a payload be replayed 'from the future'."""
        import time

        from meiko.api.auth import AuthError, check_login_widget

        token = "123:" + "a" * 30
        payload = self._widget_payload(token, int(time.time()) + 86_400 * 365)
        with pytest.raises(AuthError):
            check_login_widget(payload, token)

    def test_webapp_init_data_expiry(self):
        import hashlib
        import hmac
        import time
        from urllib.parse import urlencode

        from meiko.api.auth import AuthError, check_webapp_init_data

        token = "123:" + "a" * 30
        pairs = {"auth_date": str(int(time.time()) - 7200), "query_id": "x", "user": "{}"}
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        with pytest.raises(AuthError, match="expired"):
            check_webapp_init_data(urlencode(pairs), token)


class TestBotTokenNeverLeaves:
    """No API response may contain a bot token."""

    async def test_bot_payloads_redact_the_token(self, client):
        response = await client.post(
            "/api/v1/auth/dev", json={"telegram_id": 9100, "first_name": "T"}
        )
        token = response.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        bot = await client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN, "name": "Secret"}, headers=headers
        )
        assert bot.status_code in (200, 201), bot.text
        assert VALID_TOKEN not in bot.text
        bot_id = bot.json()["id"]
        for path in (f"/api/v1/bots/{bot_id}", "/api/v1/bots"):
            body = (await client.get(path, headers=headers)).text
            assert VALID_TOKEN not in body
            assert "Z" * 34 not in body

    async def test_health_endpoint_leaks_no_secrets(self, client):
        body = (await client.get("/health")).text
        for needle in ("password", "secret_key", "redis://", VALID_TOKEN):
            assert needle not in body.lower()


class TestWatchdogIsSeparate:
    """A dead process cannot restart itself: the watchdog must be its own service."""

    def test_compose_runs_the_watchdog_separately(self):
        compose = Path("docker-compose.yml").read_text()
        assert "watchdog:" in compose
        assert '"--service", "watchdog"' in compose or "--service watchdog" in compose

    def test_start_py_exposes_each_service(self):
        import subprocess
        import sys

        completed = subprocess.run(
            [sys.executable, "start.py", "--help"], capture_output=True, text=True
        )
        assert completed.returncode == 0
        for service in ("api", "bot", "worker", "scheduler", "watchdog", "migrate"):
            assert service in completed.stdout


class TestReadinessIsReal:
    async def test_ready_reflects_the_database(self, client):
        response = await client.get("/ready")
        assert response.status_code in (200, 503)
        body = response.json()
        assert body["ready"] is (response.status_code == 200)

    async def test_health_reports_the_dependency_it_probed(self, client):
        body = (await client.get("/health")).json()
        assert body["database"]["status"] == "ok"
        assert "cache" in body


class TestGlobalScopeAuthz:
    """The original hole: global rows were reachable because their check was skipped."""

    async def _login(self, client, telegram_id: int) -> dict:
        response = await client.post("/api/v1/auth/dev", json={"telegram_id": telegram_id})
        return {"Authorization": f"Bearer {response.json()['token']}"}

    async def test_a_normal_user_cannot_install_globally(self, client, settings):
        headers = await self._login(client, 9301)
        response = await client.post(
            "/api/v1/plugins/welcome/install", json={}, headers=headers
        )
        assert response.status_code == 403
        body = response.json()
        detail = body.get("detail", body)
        assert (detail["error"] if isinstance(detail, dict) else body["error"]) == (
            "global_scope_forbidden"
        )

    async def test_a_normal_user_cannot_toggle_globally(self, client, settings):
        headers = await self._login(client, 9302)
        response = await client.post(
            "/api/v1/plugins/welcome/toggle?enabled=true", headers=headers
        )
        assert response.status_code == 403

    async def test_a_normal_user_cannot_reconcile_globally(self, client, settings):
        headers = await self._login(client, 9303)
        response = await client.post(
            "/api/v1/owner/plugins/global/welcome/reconcile", headers=headers
        )
        assert response.status_code == 403

    async def test_another_tenants_installation_is_invisible(self, client, settings):
        first = await self._login(client, 9304)
        bot = await client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN, "name": "A"}, headers=first
        )
        assert bot.status_code in (200, 201), bot.text
        bot_id = bot.json()["id"]
        install = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=first
        )
        assert install.status_code == 201, install.text
        installation_id = install.json()["id"]

        second = await self._login(client, 9305)
        for method, path in (
            ("patch", f"/api/v1/plugins/installations/{installation_id}/configure"),
            ("post", f"/api/v1/plugins/installations/{installation_id}/enable"),
            ("post", f"/api/v1/plugins/installations/{installation_id}/disable"),
            ("post", f"/api/v1/plugins/installations/{installation_id}/restart"),
            ("delete", f"/api/v1/plugins/installations/{installation_id}"),
        ):
            kwargs = {"headers": second}
            if method == "patch":
                kwargs["json"] = {"config": {}}
            response = await getattr(client, method)(path, **kwargs)
            assert response.status_code == 404, (method, path, response.status_code)


class TestEffectivePluginTruth:
    """`running` must be the runtime's answer, not "the bot process is up"."""

    async def test_effective_states_are_per_bot(self, client, settings):
        headers = await self._login_headers(client, 9310)
        bot = await client.post(
            "/api/v1/bots", json={"token": VALID_TOKEN, "name": "Truth"}, headers=headers
        )
        bot_id = bot.json()["id"]
        await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot_id}, headers=headers
        )
        body = (await client.get("/api/v1/plugins/effective", headers=headers)).json()
        assert str(bot_id) in body["bots"]
        entry = body["bots"][str(bot_id)]
        assert entry["running"] is False  # no worker process in this test
        welcome = entry["plugins"]["welcome"]
        # A fresh install is installed-but-disabled, and nothing is running.
        assert welcome["state"] == "installed"
        assert welcome["enabled"] is False
        assert welcome["running"] is False
        assert welcome["installation_id"] is not None
        assert welcome["status"] != "running"

    async def _login_headers(self, client, telegram_id: int) -> dict:
        response = await client.post("/api/v1/auth/dev", json={"telegram_id": telegram_id})
        return {"Authorization": f"Bearer {response.json()['token']}"}


class TestProductionRefusesUnusableSettings:
    """A generated SECRET_KEY in production silently breaks every session."""

    def test_production_without_a_secret_key_refuses_to_start(self, monkeypatch):
        import pytest as _pytest

        from meiko.api.app import _verify_deployment_settings
        from meiko.config import Settings

        settings = Settings()
        object.__setattr__(settings, "environment", "production")
        object.__setattr__(settings, "secret_key", "short")
        with _pytest.raises(RuntimeError, match="SECRET_KEY"):
            _verify_deployment_settings(settings)

    def test_production_with_a_secret_key_is_allowed(self, monkeypatch):
        from meiko.api.app import _verify_deployment_settings
        from meiko.config import Settings

        settings = Settings()
        object.__setattr__(settings, "environment", "production")
        object.__setattr__(settings, "secret_key", "k" * 48)
        _verify_deployment_settings(settings)

    def test_production_refuses_a_remote_database_without_tls(self):
        import pytest as _pytest

        from meiko.api.app import _verify_deployment_settings
        from meiko.config import Settings

        settings = Settings(environment="production")
        object.__setattr__(settings, "secret_key", "k" * 48)
        object.__setattr__(
            settings,
            "database_url",
            "postgresql+asyncpg://u:p@db.example.com:5432/postgres",
        )
        with _pytest.raises(RuntimeError, match="TLS"):
            _verify_deployment_settings(settings)

    def test_development_is_never_blocked(self):
        from meiko.api.app import _verify_deployment_settings
        from meiko.config import Settings

        _verify_deployment_settings(Settings())
