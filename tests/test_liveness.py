"""Liveness must say whether bots are actually being serviced.

"Keep-alive is bad sometimes" is almost never the timer: it is that nothing notices
a bot whose polling loop has ended (token conflict, rejected token, network gone)
while the web process still answers its health check.  These tests pin the three
independent conditions and the endpoints that expose them.
"""

from __future__ import annotations

import asyncio
import time

import pytest


@pytest.fixture
async def app_client(settings):
    from httpx import ASGITransport, AsyncClient

    from meiko.api.app import create_app
    from meiko.cache import reset_cache

    await reset_cache()
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


def _application(**kwargs):
    from meiko.bot import BotApplication

    application = BotApplication(bot_id=1, owner_id=1, token="123:abc")
    for key, value in kwargs.items():
        setattr(application, key, value)
    return application


class TestBotLiveness:
    def test_a_fresh_application_is_not_alive(self):
        """Nothing has polled yet, so it must not claim to be running."""
        check = _application().liveness()
        assert check["alive"] is False
        assert "polling has not started" in check["reasons"]

    def test_a_polling_loop_that_ended_is_not_alive(self):
        async def scenario():
            application = _application()
            application._polling_task = asyncio.get_running_loop().create_future()
            application._polling_task.set_result(None)  # the loop returned
            application.last_poll_at = time.time()
            application.healthy = True
            return application.liveness()

        check = asyncio.run(scenario())
        assert check["alive"] is False
        assert any("not running" in reason for reason in check["reasons"])

    def test_a_healthy_flag_alone_is_not_enough(self):
        """`healthy` is set at start; a loop that stalled for an hour still says True."""
        application = _application(
            healthy=True, last_poll_at=time.time() - 3600, _polling_task=None
        )
        check = application.liveness()
        assert check["alive"] is False
        assert check["last_poll_age_seconds"] >= 3500

    def test_all_conditions_met_is_alive(self):
        async def scenario():
            application = _application()
            application._polling_task = asyncio.get_running_loop().create_task(asyncio.sleep(30))
            application.last_poll_at = time.time()
            application.healthy = True
            result = application.liveness()
            application._polling_task.cancel()
            return result

        check = asyncio.run(scenario())
        assert check["alive"] is True, check["reasons"]
        assert check["reasons"] == []

    def test_the_staleness_window_scales_with_the_poll_timeout(self):
        async def scenario():
            application = _application(healthy=True, last_poll_at=time.time() - 100)
            application._polling_task = asyncio.get_running_loop().create_task(asyncio.sleep(30))
            try:
                return (
                    application.liveness(stale_after=200)["alive"],
                    application.liveness(stale_after=50)["alive"],
                )
            finally:
                application._polling_task.cancel()

        fresh_enough, too_stale = asyncio.run(scenario())
        assert fresh_enough is True
        assert too_stale is False

    def test_health_surfaces_liveness(self):
        application = _application()
        payload = application.health()
        assert payload["alive"] is False
        assert payload["status"] == "stopped"
        assert "liveness" in payload


class TestHealthEndpoints:
    async def test_health_reports_bot_counts(self, app_client):
        body = (await app_client.get("/health")).json()
        assert "bots" in body
        assert set(body["bots"]) >= {"workers", "alive", "stale", "errors", "serving"}

    async def test_health_bots_is_ok_with_no_bots(self, app_client):
        response = await app_client.get("/health/bots")
        assert response.status_code == 200
        assert response.json()["healthy"] is True

    async def test_health_bots_fails_when_a_bot_is_not_serviced(self, app_client, monkeypatch):
        from meiko.runtime.manager import get_worker_manager

        manager = get_worker_manager()
        manager.state.applications[99] = _application()
        try:
            response = await app_client.get("/health/bots")
            assert response.status_code == 503
            body = response.json()
            assert body["healthy"] is False
            assert body["bots"]["99"]["alive"] is False
            # /health must not claim a serving platform either
            summary = (await app_client.get("/health")).json()["bots"]
            assert summary["stale"] == [99]
            assert summary["serving"] is False
        finally:
            manager.state.applications.pop(99, None)


class TestPlatformPort:
    def test_port_is_adopted_from_the_platform(self, monkeypatch):
        from meiko.config import Settings

        monkeypatch.delenv("API_PORT", raising=False)
        monkeypatch.setenv("PORT", "10000")
        monkeypatch.setenv("MEIKO_ENV_FILE", "")
        assert Settings().api_port == 10000

    def test_an_explicit_api_port_wins(self, monkeypatch):
        from meiko.config import Settings

        monkeypatch.setenv("PORT", "10000")
        monkeypatch.setenv("API_PORT", "9001")
        monkeypatch.setenv("MEIKO_ENV_FILE", "")
        assert Settings().api_port == 9001

    def test_a_junk_port_is_ignored(self, monkeypatch):
        from meiko.config import Settings

        monkeypatch.delenv("API_PORT", raising=False)
        monkeypatch.setenv("PORT", "not-a-port")
        monkeypatch.setenv("MEIKO_ENV_FILE", "")
        assert Settings().api_port == 8000


class TestTheBlueprintIsHonest:
    """render.yaml must not encode the thing that got the accounts suspended."""

    @pytest.fixture(scope="class")
    def blueprint(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "render.yaml").read_text()

    def test_it_does_not_ask_for_a_sleeping_free_plan(self, blueprint):
        import re

        # comments explain why not; only real settings count
        settings = [
            line.split("#", 1)[0].strip()
            for line in blueprint.splitlines()
            if line.strip().startswith("plan:")
        ]
        assert settings, "render.yaml declares no plan"
        assert all("free" not in value for value in settings), settings
        assert any("starter" in value for value in settings), settings

    def test_the_health_check_is_a_real_endpoint(self, blueprint):
        assert "healthCheckPath: /health/live" in blueprint
        # and the app really serves it
        from meiko.api.app import create_app

        assert "/health/live" in create_app().openapi()["paths"]

    def test_it_does_not_ping_itself(self, blueprint):
        """No self-ping URL, no cron that hits the service, no keep-alive timer."""
        lowered = "\n".join(
            line.split("#", 1)[0].lower() for line in blueprint.splitlines()
        )
        for word in ("ping", "keep_alive", "keepalive", "cron"):
            assert word not in lowered, word
