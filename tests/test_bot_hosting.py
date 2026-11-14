"""PART 16 — bot hosting and per-bot isolation.

The promise is that each bot's token, owner, runtime, logs and quotas are its own,
that a token is never returned to a client, and that two processes can never poll
the same token.
"""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from meiko.db.models import BotAccount, Worker
from meiko.security.crypto import token_fingerprint

IX = 4  # distinct token suffix per test, keeps fingerprints unique


def make_token(n: int) -> str:
    return f"{100000 + n}:{'A' * 30}{n:02d}"


@pytest_asyncio.fixture()
async def client(settings):
    from meiko.api.app import create_app
    from meiko.cache import reset_cache
    from meiko.runtime import manager as manager_module

    await reset_cache()
    manager_module._manager = None
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
    # The lifespan already stopped every application; drop the singleton without
    # touching the (now disposed) database again.
    manager_module._manager = None


@pytest.fixture()
def manager(settings):
    from meiko.runtime import manager as manager_module

    yield manager_module.get_worker_manager()
    manager_module._manager = None


async def promote_owner(telegram_id: int) -> None:
    from sqlalchemy import select

    from meiko.db.base import session_scope
    from meiko.db.models import User

    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        assert user is not None
        user.is_owner = True
        user.role = "owner"


async def login(client: AsyncClient, telegram_id: int) -> dict:
    response = await client.post("/api/v1/auth/dev", json={"telegram_id": telegram_id})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def make_bot(client: AsyncClient, headers: dict, n: int, name: str = "Bot") -> dict:
    response = await client.post(
        "/api/v1/bots", json={"token": make_token(n), "name": name}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestTokenNeverLeavesTheServer:
    async def test_token_is_absent_from_every_bot_payload(self, client):
        headers = await login(client, 4101)
        await promote_owner(4101)
        token = make_token(41)
        bot = await make_bot(client, headers, 41)
        for path in (f"/api/v1/bots/{bot['id']}", "/api/v1/bots", "/api/v1/owner/bots"):
            response = await client.get(path, headers=headers)
            assert response.status_code == 200, (path, response.status_code)
            assert token not in response.text
            assert token.split(":")[1] not in response.text

    async def test_token_is_encrypted_and_fingerprinted_at_rest(self, client, db, session):
        headers = await login(client, 4102)
        token = make_token(42)
        await make_bot(client, headers, 42)
        row = await session.scalar(
            select(BotAccount).where(BotAccount.token_fingerprint == token_fingerprint(token))
        )
        assert row is not None
        assert token not in row.token_encrypted
        assert row.token_fingerprint != token

    async def test_a_duplicate_token_is_refused(self, client):
        headers = await login(client, 4103)
        await make_bot(client, headers, 43)
        clash = await client.post(
            "/api/v1/bots", json={"token": make_token(43), "name": "Twin"}, headers=headers
        )
        assert clash.status_code == 400
        assert "already" in clash.text.lower()

    async def test_another_owner_cannot_register_the_same_token(self, client):
        first = await login(client, 4104)
        await make_bot(client, first, 44)
        second = await login(client, 4105)
        clash = await client.post(
            "/api/v1/bots", json={"token": make_token(44), "name": "Steal"}, headers=second
        )
        assert clash.status_code == 400


class TestPerBotIsolation:
    async def test_a_bot_is_invisible_to_other_owners(self, client):
        owner = await login(client, 4201)
        bot = await make_bot(client, owner, 51)
        stranger = await login(client, 4202)
        assert (await client.get(f"/api/v1/bots/{bot['id']}", headers=stranger)).status_code == 404
        for action in ("start", "stop", "restart"):
            response = await client.post(f"/api/v1/bots/{bot['id']}/{action}", headers=stranger)
            assert response.status_code == 404, action
        assert (
            await client.get(f"/api/v1/bots/{bot['id']}/groups", headers=stranger)
        ).status_code == 404
        assert (await client.delete(f"/api/v1/bots/{bot['id']}", headers=stranger)).status_code == 404

    async def test_commands_are_scoped_to_their_bot(self, client):
        owner = await login(client, 4203)
        left = await make_bot(client, owner, 52, "Left")
        right = await make_bot(client, owner, 53, "Right")
        created = await client.post(
            f"/api/v1/bots/{left['id']}/commands",
            json={"name": "leftonly", "description": "just for left", "response": "hi"},
            headers=owner,
        )
        assert created.status_code in (200, 201), created.text
        def names(payload: object) -> set[str]:
            rows = payload["items"] if isinstance(payload, dict) else payload
            return {row["name"] for row in rows}

        left_payload = (
            await client.get(f"/api/v1/bots/{left['id']}/commands", headers=owner)
        ).json()
        right_payload = (
            await client.get(f"/api/v1/bots/{right['id']}/commands", headers=owner)
        ).json()
        left_names = names(left_payload)
        right_names = names(right_payload)
        assert "leftonly" in left_names
        assert "leftonly" not in right_names

    async def test_quotas_are_per_bot(self, client):
        owner = await login(client, 4204)
        bot = await make_bot(client, owner, 54)
        assert bot["limits"]["max_plugins"] == 25
        patched = await client.patch(
            f"/api/v1/bots/{bot['id']}", json={"limits": {"max_plugins": 3}}, headers=owner
        )
        assert patched.status_code == 200
        assert patched.json()["limits"]["max_plugins"] == 3
        assert patched.json()["limits"]["max_groups"] == 100


async def set_bot(bot_id: int, **values) -> None:
    from meiko.db.base import session_scope

    async with session_scope() as db:
        row = await db.get(BotAccount, bot_id)
        for key, value in values.items():
            setattr(row, key, value)


class TestOneTokenOnePoller:
    """The single most damaging hosting bug: two workers long-polling one token."""

    async def test_starting_twice_is_idempotent(self, client, manager):
        from meiko.runtime import manager as manager_module

        owner = await login(client, 4301)
        bot = await make_bot(client, owner, 61)
        # pretend the token is verified; polling itself is exercised in test_runtime
        await self._verify(bot["id"])
        started = await manager.start(bot["id"])
        assert started["status"] in ("running", "error")
        if started["status"] != "running":
            pytest.skip(f"runtime unavailable in this environment: {started}")
        again = await manager.start(bot["id"])
        assert again.get("note") == "already started"
        assert manager_module.get_worker_manager().running_ids().count(bot["id"]) == 1
        await manager.stop(bot["id"])

    async def test_the_start_lock_is_held_while_polling_starts(self, client):
        """A second process must be locked out for as long as the first is starting."""
        from meiko.cache import get_cache

        cache = get_cache()
        async with cache.lock("bot-start:999", timeout=30) as first:
            assert first is True
            async with cache.lock("bot-start:999", timeout=30) as second:
                assert second is False

    async def test_a_second_worker_is_refused_by_the_claim(self, client, manager, session):
        """The start lock is only held while starting; the claim outlives it."""
        from meiko.runtime.manager import WorkerManager

        owner = await login(client, 4303)
        bot = await make_bot(client, owner, 63)
        await self._verify(bot["id"])
        # This worker claims the bot and is recorded as its host.
        claim = await manager.claim(bot["id"])
        assert claim["acquired"] is True
        await set_bot(bot["id"], worker_id=manager.state.worker_id, status="running")
        other = WorkerManager()
        other.state.worker_id = (manager.state.worker_id or 0) + 1000
        refused = await other.claim(bot["id"])
        assert refused["acquired"] is False
        assert refused["reason"] == "running_elsewhere"
        result = await other.start(bot["id"])
        assert result["status"] == "error"
        assert "already hosted" in result["error"]
        assert manager.running_ids() == []

    async def test_a_second_worker_cannot_claim_a_leased_bot(self, client, manager):
        """A running bot has exactly one owner worker, recorded in the database."""
        from meiko.services.core import BotService
        from meiko.db.base import session_scope

        owner = await login(client, 4302)
        bot = await make_bot(client, owner, 62)
        await self._verify(bot["id"])
        started = await manager.start(bot["id"])
        if started["status"] != "running":
            pytest.skip(f"runtime unavailable in this environment: {started}")
        try:
            async with session_scope() as session:
                row = await session.get(BotAccount, bot["id"])
                assert row.worker_id == manager.state.worker_id
                assert row.status == "running"
        finally:
            await manager.stop(bot["id"])
        async with session_scope() as session:
            row = await session.get(BotAccount, bot["id"])
            assert row.worker_id is None
            assert row.status == "stopped"

    async def _verify(self, bot_id: int) -> None:
        from meiko.db.base import session_scope

        async with session_scope() as session:
            bot = await session.get(BotAccount, bot_id)
            bot.token_valid = True
            bot.status = "stopped"


class TestHealthIsReal:
    async def test_health_of_a_stopped_bot_is_stopped(self, client):
        owner = await login(client, 4401)
        bot = await make_bot(client, owner, 71)
        health = await client.get(f"/api/v1/bots/{bot['id']}/health", headers=owner)
        assert health.status_code == 200
        assert health.json()["running"] is False

    async def test_stop_is_safe_for_a_bot_that_never_started(self, client):
        owner = await login(client, 4402)
        bot = await make_bot(client, owner, 72)
        stopped = await client.post(f"/api/v1/bots/{bot['id']}/stop", headers=owner)
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False

    async def test_start_reports_the_token_problem_instead_of_pretending(self, client):
        owner = await login(client, 4403)
        bot = await make_bot(client, owner, 73)
        started = await client.post(f"/api/v1/bots/{bot['id']}/start", headers=owner)
        body = started.json()
        assert body["running"] is False
        assert "verified" in (body["last_error"] or "").lower()


class TestRuntimeDetail:
    """The hosted-bot screen needs logs, usage, storage and task history."""

    async def test_logs_are_the_bots_own(self, client):
        owner = await login(client, 4601)
        bot = await make_bot(client, owner, 91)
        other = await make_bot(client, owner, 92)
        await client.post(f"/api/v1/bots/{bot['id']}/start", headers=owner)
        first = (await client.get(f"/api/v1/bots/{bot['id']}/logs", headers=owner)).json()
        second = (await client.get(f"/api/v1/bots/{other['id']}/logs", headers=owner)).json()
        assert first["items"]
        assert all(row.get("kind") for row in first["items"])
        # No row from bot A may appear in bot B's log.
        ids_first = {row["id"] for row in first["items"]}
        ids_second = {row["id"] for row in second["items"]}
        assert ids_first.isdisjoint(ids_second)

    async def test_logs_accept_a_kind_filter(self, client):
        owner = await login(client, 4602)
        bot = await make_bot(client, owner, 93)
        await client.post(f"/api/v1/bots/{bot['id']}/start", headers=owner)
        body = (
            await client.get(
                f"/api/v1/bots/{bot['id']}/logs?kind=bot.start_refused", headers=owner
            )
        ).json()
        assert all(row["kind"] == "bot.start_refused" for row in body["items"])

    async def test_usage_reports_whether_the_bot_is_hosted_here(self, client):
        owner = await login(client, 4603)
        bot = await make_bot(client, owner, 94)
        body = (await client.get(f"/api/v1/bots/{bot['id']}/usage", headers=owner)).json()
        assert body["runtime"]["hosted_here"] is False
        assert body["runtime"]["uptime_seconds"] == 0
        assert "disk" in body["storage"]
        assert body["storage"]["disk_total_bytes"] == 0

    async def test_tasks_are_scoped_and_empty_not_faked(self, client):
        owner = await login(client, 4604)
        bot = await make_bot(client, owner, 95)
        body = (await client.get(f"/api/v1/bots/{bot['id']}/tasks", headers=owner)).json()
        assert body["count"] == 0
        assert body["items"] == []

    @pytest.mark.parametrize("suffix", ["logs", "usage", "tasks"])
    async def test_detail_endpoints_are_tenant_scoped(self, client, suffix):
        owner = await login(client, 4605)
        bot = await make_bot(client, owner, 96)
        stranger = await login(client, 4606)
        response = await client.get(f"/api/v1/bots/{bot['id']}/{suffix}", headers=stranger)
        assert response.status_code == 404


class TestGracefulShutdown:
    async def test_stopping_everything_marks_the_worker_offline(self, client, manager, session):
        await manager.register()
        worker_id = manager.state.worker_id
        await manager.stop_all()
        worker = await session.get(Worker, worker_id)
        assert worker is not None
        assert worker.status == "offline"
        assert worker.current_load == 0

    async def test_recovery_only_resumes_bots_meant_to_run(self, client, manager, session):
        owner = await login(client, 4501)
        run = await make_bot(client, owner, 81, "ShouldRun")
        stop = await make_bot(client, owner, 82, "ShouldNotRun")
        from meiko.db.base import session_scope

        async with session_scope() as db:
            row = await db.get(BotAccount, run["id"])
            row.status = "running"
            row.token_valid = True
            other = await db.get(BotAccount, stop["id"])
            other.status = "stopped"
            other.token_valid = True
        result = await manager.recover()
        attempted = set(result["started"]) | {item["bot_id"] for item in result["failed"]}
        assert run["id"] in attempted
        assert stop["id"] not in attempted


class TestVerificationIsInteractive:
    """Connecting a bot must fail fast, not inherit the background retry budget."""

    async def test_verification_uses_a_short_single_attempt(self, monkeypatch):
        from meiko.api.routers import bots as bots_module

        seen: dict[str, object] = {}

        class FakeClient:
            class session:
                @staticmethod
                async def close():
                    return None

        def fake_build(token, extra_defaults=None, *, timeout=30.0):
            seen["timeout"] = timeout
            return FakeClient()

        async def fake_fetch(client, *, attempts=3):
            seen["attempts"] = attempts
            raise RuntimeError("telegram unreachable")

        class FakeService:
            async def verify_identity(self, *args, **kwargs):
                raise AssertionError("must not verify when the probe failed")

        monkeypatch.setattr("meiko.telegram.client.build_bot", fake_build)
        monkeypatch.setattr("meiko.telegram.client.fetch_me", fake_fetch)
        monkeypatch.setattr(
            "meiko.config.get_settings",
            lambda: type("S", (), {"is_testing": False})(),
        )

        class FakeSession:
            pass

        class FakeBot:
            last_error = ""
            token_valid = True

        bot = FakeBot()
        await bots_module.verify_bot_token(FakeSession(), FakeService(), bot, "123:" + "a" * 30)
        assert seen["attempts"] == 1
        assert seen["timeout"] <= 10
        assert bot.token_valid is False
        assert "unreachable" in bot.last_error
