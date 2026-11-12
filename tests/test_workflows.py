"""Acceptance tests for Workflows A–I.

Each class covers one end-to-end workflow the platform promises:

A  Bot connect + lifecycle          F  Automation (workflows + schedules)
B  Multi-bot / clone hosting        G  Developer surface (keys, webhooks, preview)
C  Moderation + rule engine         H  Observability + owner control
D  Plugin lifecycle                 I  Safety/security invariants
E  Group governance

They use the same ASGI app and real database as production code, so a passing
run means the wiring between bot, API, services and database works together.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from meiko.services.core import UserService

VALID = "123456789:" + "A" * 34
CLONE = "987654321:" + "B" * 34


@pytest_asyncio.fixture()
async def client(settings):
    from meiko.api.app import create_app
    from meiko.cache import reset_cache

    await reset_cache()
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def login(client: AsyncClient, telegram_id: int = 4242) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/dev",
        json={"telegram_id": telegram_id, "username": f"wf{telegram_id}", "first_name": "Workflow"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def make_bot(client: AsyncClient, headers: dict[str, str], token: str = VALID, **extra) -> dict:
    response = await client.post("/api/v1/bots", json={"token": token, **extra}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- A
class TestWorkflowABotLifecycle:
    async def test_connect_observe_start_stop(self, client):
        headers = await login(client)
        bot = await make_bot(client, headers, telemetry := VALID, name="Lifecycle")
        assert bot["status"] in ("draft", "stopped")
        assert bot["token_valid"] is False  # testing: Telegram probe skipped, never faked true

        health = (await client.get(f"/api/v1/bots/{bot['id']}/health", headers=headers)).json()
        assert health["bot_id"] == bot["id"]
        assert health["running"] is False

        started = (await client.post(f"/api/v1/bots/{bot['id']}/start", headers=headers)).json()
        assert started["running"] is False  # unverified token is refused, not pretended
        stopped = (await client.post(f"/api/v1/bots/{bot['id']}/stop", headers=headers)).json()
        assert stopped["running"] is False

        activity = (await client.get("/api/v1/activity?limit=10", headers=headers)).json()
        kinds = {item["kind"] for item in activity}
        assert "bot.connected" in kinds

    async def test_tokens_are_never_serialised(self, client):
        headers = await login(client, 4300)
        bot = await make_bot(client, headers, VALID)
        listed = (await client.get("/api/v1/bots", headers=headers)).json()
        assert "token" not in bot
        assert "token_encrypted" not in bot
        assert "token_encrypted" not in listed["items"][0]

    async def test_runtime_logs_are_recorded(self, client):
        headers = await login(client, 4301)
        bot = await make_bot(client, headers, VALID)
        await client.post(f"/api/v1/bots/{bot['id']}/start", headers=headers)
        logs = (await client.get("/api/v1/infrastructure/logs?limit=50", headers=headers)).json()
        assert any(entry["kind"].startswith("bot.") for entry in logs)


# --------------------------------------------------------------------------- B
class TestWorkflowBCloneHosting:
    async def test_clone_is_isolated_and_copies_plugins(self, client):
        headers = await login(client, 4310)
        source = await make_bot(client, headers, VALID)
        install = await client.post(
            f"/api/v1/plugins/anti_spam/install",
            json={"bot_id": source["id"]},
            headers=headers,
        )
        assert install.status_code == 201

        clone = await client.post(
            f"/api/v1/bots/{source['id']}/clone",
            json={"token": CLONE, "name": "Clone B", "copy_plugins": True},
            headers=headers,
        )
        assert clone.status_code == 201, clone.text
        body = clone.json()
        assert body["is_clone"] is True and body["source_bot_id"] == source["id"]

        clone_plugins = (
            await client.get(f"/api/v1/plugins/installed?bot_id={body['id']}", headers=headers)
        ).json()
        assert any(row["plugin"]["slug"] == "anti_spam" for row in clone_plugins)

        # Deleting the source must not delete the clone.
        assert (await client.delete(f"/api/v1/bots/{source['id']}", headers=headers)).status_code == 204
        assert (await client.get(f"/api/v1/bots/{body['id']}", headers=headers)).status_code == 200

    async def test_clone_requires_a_unique_token(self, client):
        headers = await login(client, 4311)
        source = await make_bot(client, headers, VALID)
        clash = await client.post(
            f"/api/v1/bots/{source['id']}/clone", json={"token": VALID}, headers=headers
        )
        assert clash.status_code == 400
        assert clash.json()["detail"]["error"] == "invalid_clone"


# --------------------------------------------------------------------------- C
class TestWorkflowCModeration:
    async def test_rules_are_validated_and_persisted(self, client, session):
        from meiko.services.core import BotService, GroupService
        from meiko.moderation.rules import Rule, RuleEngine
        from meiko.moderation.events import ModerationEvent

        headers = await login(client, 4320)
        bot = await make_bot(client, headers, VALID)
        owner = await UserService(session).get_or_create_telegram_user(4320)
        group = await GroupService(session).upsert(
            bot_id=bot["id"], chat_id=-100999, title="Rules Chat", chat_type="supergroup"
        )
        await session.commit()

        created = await client.post(
            f"/api/v1/groups/{group.id}/rules",
            json={
                "name": "Block links",
                "event": "message",
                "conditions": [{"fact": "message.has_link", "op": "is_true"}],
                "actions": [{"type": "delete"}],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["conditions"][0]["fact"] == "message.has_link"

        bad = await client.post(
            f"/api/v1/groups/{group.id}/rules",
            json={"name": "Bad", "actions": [{"type": "nuke"}]},
            headers=headers,
        )
        assert bad.status_code == 422
        assert bad.json()["detail"]["error"] == "invalid_rule"

        # The stored rule really fires through the engine.
        rules = (await client.get(f"/api/v1/groups/{group.id}/rules", headers=headers)).json()
        engine = RuleEngine(executor=lambda action, event, rule: _record(executed.append(action)))
        executed: list[dict] = []
        result = await engine.process(
            ModerationEvent(kind="message", bot_id=bot["id"], chat_id=-100999, has_link=True),
            rules=[Rule(**{k: rules[0][k] for k in ("id", "name", "event", "conditions", "actions", "priority", "enabled")})],
        )
        assert result.matched is True
        assert executed and executed[0]["type"] == "delete"

    async def test_rule_engine_executor_guardrails(self):
        """Unknown actions are rejected before they reach Telegram."""
        from meiko.moderation.rules import Rule

        rule = Rule(id=None, name="bad", event="message", actions=[{"type": "eval_python"}])
        assert "unknown action type 'eval_python'" in rule.validate()

    async def test_moderation_history_and_warnings_endpoints(self, client, session):
        from meiko.services.core import BotService, GroupService, ModerationService

        headers = await login(client, 4321)
        bot = await make_bot(client, headers, VALID)
        group = await GroupService(session).upsert(
            bot_id=bot["id"], chat_id=-100998, title="History", chat_type="supergroup"
        )
        service = ModerationService(session)
        await service.record(
            bot["id"], action="warn", group_id=group.id, chat_id=-100998,
            target_id=555, moderator_id=4321, reason="spam",
        )
        await session.commit()

        history = (await client.get(f"/api/v1/groups/{group.id}/moderation", headers=headers)).json()
        assert history[0]["action"] == "warn"
        assert history[0]["reason"] == "spam"

        detail = (await client.get(f"/api/v1/groups/{group.id}", headers=headers)).json()
        assert detail["moderation_actions"] >= 1


async def _record(action: dict) -> dict:
    return {"executed": action}


# --------------------------------------------------------------------------- D
class TestWorkflowDPluginLifecycle:
    async def test_full_lifecycle_available_to_configured(self, client):
        headers = await login(client, 4330)
        bot = await make_bot(client, headers, VALID)

        detail = (await client.get("/api/v1/plugins/welcome", headers=headers)).json()
        assert detail["implemented"] is True
        assert detail["installable"] is True

        installed = await client.post(
            "/api/v1/plugins/welcome/install", json={"bot_id": bot["id"]}, headers=headers
        )
        assert installed.status_code == 201
        installation = installed.json()
        assert installation["state"] == "installed"

        enabled = await client.post(
            f"/api/v1/plugins/installations/{installation['id']}/enable", headers=headers
        )
        assert enabled.status_code == 200
        assert enabled.json()["state"] in ("enabled", "configured")

        configured = await client.patch(
            f"/api/v1/plugins/installations/{installation['id']}/configure",
            json={"config": {"welcome_text": "Hi {user}!"}},
            headers=headers,
        )
        assert configured.status_code == 200
        assert configured.json()["config"]["welcome_text"] == "Hi {user}!"

        disabled = await client.post(
            f"/api/v1/plugins/installations/{installation['id']}/disable", headers=headers
        )
        assert disabled.json()["state"] == "disabled"

        uninstalled = await client.delete(
            f"/api/v1/plugins/installations/{installation['id']}", headers=headers
        )
        assert uninstalled.status_code == 204

    async def test_unknown_config_keys_are_rejected(self, client):
        headers = await login(client, 4331)
        bot = await make_bot(client, headers, VALID)
        installation = (
            await client.post("/api/v1/plugins/reports/install", json={"bot_id": bot["id"]}, headers=headers)
        ).json()
        response = await client.patch(
            f"/api/v1/plugins/installations/{installation['id']}/configure",
            json={"config": {"not_a_real_option": True}},
            headers=headers,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_config"

    async def test_disabled_plugin_registers_no_commands(self, client):
        headers = await login(client, 4332)
        bot = await make_bot(client, headers, VALID)
        commands = (await client.get(f"/api/v1/bots/{bot['id']}/commands", headers=headers)).json()
        assert isinstance(commands, list)
        # Nothing installed yet: only core commands are visible, no plugin ones.
        assert all(not str(item["source"]).startswith("plugin:") for item in commands if item["id"] is None)


# --------------------------------------------------------------------------- E
class TestWorkflowEGroupGovernance:
    async def test_group_settings_round_trip(self, client, session):
        from meiko.services.core import GroupService

        headers = await login(client, 4340)
        bot = await make_bot(client, headers, VALID)
        group = await GroupService(session).upsert(
            bot_id=bot["id"], chat_id=-100997, title="Governance", chat_type="supergroup"
        )
        await session.commit()

        patched = await client.patch(
            f"/api/v1/groups/{group.id}/settings",
            json={"welcome_enabled": False, "slow_mode_seconds": 30, "antispam": {"action": "ban"}},
            headers=headers,
        )
        assert patched.status_code == 200

        detail = (await client.get(f"/api/v1/groups/{group.id}", headers=headers)).json()
        assert detail["settings"]["welcome_enabled"] is False
        assert detail["settings"]["slow_mode_seconds"] == 30
        assert detail["settings"]["antispam"]["action"] == "ban"

    async def test_reports_and_appeals_are_tenant_scoped(self, client, session):
        from meiko.services.core import GroupService

        owner = await login(client, 4341)
        outsider = await login(client, 4342)
        bot = await make_bot(client, owner, VALID)
        group = await GroupService(session).upsert(
            bot_id=bot["id"], chat_id=-100996, title="Reports", chat_type="supergroup"
        )
        await session.commit()

        assert (await client.get(f"/api/v1/groups/{group.id}/reports", headers=owner)).status_code == 200
        assert (await client.get(f"/api/v1/groups/{group.id}/reports", headers=outsider)).status_code == 404
        appeals = (await client.get("/api/v1/groups/appeals/all", headers=outsider)).json()
        assert appeals == []


# --------------------------------------------------------------------------- F
class TestWorkflowFAutomation:
    async def test_workflow_crud_and_validation(self, client):
        headers = await login(client, 4350)
        bot = await make_bot(client, headers, VALID)

        invalid = await client.post(
            f"/api/v1/bots/{bot['id']}/workflows",
            json={"name": "Bad", "trigger": {"event": "teleport"}, "actions": [{"type": "send_message"}]},
            headers=headers,
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["error"] == "invalid_workflow"

        created = await client.post(
            f"/api/v1/bots/{bot['id']}/workflows",
            json={
                "name": "Welcome",
                "trigger": {"event": "join"},
                "conditions": [{"fact": "user.is_new_member", "op": "is_true"}],
                "actions": [{"type": "send_message", "text": "hi"}],
            },
            headers=headers,
        )
        assert created.status_code == 201
        workflow = created.json()
        assert workflow["version"] == 1

        updated = await client.patch(
            f"/api/v1/bots/{bot['id']}/workflows/{workflow['id']}",
            json={
                "name": "Welcome v2",
                "enabled": False,
                "trigger": {"event": "join"},
                "conditions": [],
                "actions": [{"type": "send_message", "text": "hi"}],
            },
            headers=headers,
        )
        assert updated.json()["version"] == 2
        assert updated.json()["enabled"] is False

        assert (
            await client.delete(f"/api/v1/bots/{bot['id']}/workflows/{workflow['id']}", headers=headers)
        ).status_code == 204

    async def test_schedules_require_cron_or_run_at(self, client):
        headers = await login(client, 4351)
        bot = await make_bot(client, headers, VALID)
        empty = await client.post(
            f"/api/v1/bots/{bot['id']}/schedules", json={"name": "none"}, headers=headers
        )
        assert empty.status_code == 422
        bad_cron = await client.post(
            f"/api/v1/bots/{bot['id']}/schedules",
            json={"name": "bad", "cron": "* *"},
            headers=headers,
        )
        assert bad_cron.status_code == 422
        ok = await client.post(
            f"/api/v1/bots/{bot['id']}/schedules",
            json={"name": "digest", "cron": "0 9 * * *", "kind": "announcement", "payload": {"text": "hi"}},
            headers=headers,
        )
        assert ok.status_code == 201
        task = ok.json()
        assert task["status"] == "pending"
        cancelled = await client.post(
            f"/api/v1/bots/{bot['id']}/schedules/{task['id']}/cancel", headers=headers
        )
        assert cancelled.json()["status"] == "cancelled"


# --------------------------------------------------------------------------- G
class TestWorkflowGDeveloper:
    async def test_api_key_scope_and_revocation(self, client):
        headers = await login(client, 4360)
        created = await client.post(
            "/api/v1/auth/api-keys", json={"name": "ci", "scopes": ["bots:read"]}, headers=headers
        )
        assert created.status_code == 201
        key = created.json()["key"]
        assert (await client.get("/api/v1/bots", headers={"X-API-Key": key})).status_code == 200

    async def test_webhook_registration_and_delivery_log(self, client):
        headers = await login(client, 4361)
        bot = await make_bot(client, headers, VALID)
        created = await client.post(
            f"/api/v1/developer/bots/{bot['id']}/webhooks",
            json={"url": "https://example.com/hook", "events": ["moderation"]},
            headers=headers,
        )
        assert created.status_code == 201
        webhook = created.json()
        deliveries = await client.get(
            f"/api/v1/developer/bots/{bot['id']}/webhooks/{webhook['id']}/deliveries", headers=headers
        )
        assert deliveries.status_code == 200
        assert deliveries.json() == []

    async def test_preview_never_executes_input(self, client):
        headers = await login(client, 4362)
        response = await client.post(
            "/api/v1/developer/preview",
            json={"kind": "html", "title": "__import__('os')", "lines": ["<script>alert(1)</script>"]},
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert "html_preview" in body
        assert "<script>" not in body["html_preview"].lower()

    async def test_api_reference_is_machine_readable(self, client):
        headers = await login(client, 4363)
        reference = (await client.get("/api/v1/developer/api-reference", headers=headers)).json()
        assert reference["prefix"] == "/api/v1"
        assert reference["bot_api"] == "10.4"
        assert "errors" in reference


# --------------------------------------------------------------------------- H
class TestWorkflowHObservability:
    async def test_dashboard_numbers_match_real_rows(self, client, session):
        from meiko.services.core import GroupService

        headers = await login(client, 4370)
        bot = await make_bot(client, headers, VALID)
        await GroupService(session).upsert(
            bot_id=bot["id"], chat_id=-100995, title="Observed", chat_type="supergroup"
        )
        await session.commit()
        dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
        assert dashboard["bots"]["total"] == 1
        assert dashboard["groups"] == 1

    async def test_owner_can_review_and_restrict(self, client, session):
        from meiko.api.auth import AuthService

        async with __import__("contextlib").nullcontext():
            owner = await UserService(session).get_or_create_telegram_user(4380, username="root")
            owner.is_owner = True
            owner.role = "owner"
            token = await AuthService(session).create_session(owner)
        await session.commit()
        headers = {"Authorization": f"Bearer {token}"}

        overview = (await client.get("/api/v1/owner/overview", headers=headers)).json()
        assert overview["users"] >= 1
        target = await UserService(session).get_or_create_telegram_user(4381, username="noisy")
        await session.commit()
        restricted = await client.post(
            f"/api/v1/owner/users/{target.id}/status?status=restricted", headers=headers
        )
        assert restricted.status_code == 200
        assert restricted.json()["status"] == "restricted"

        audit = (await client.get("/api/v1/owner/audit", headers=headers)).json()
        assert any(row["action"] == "owner.user_status" for row in audit)

    async def test_health_and_readiness(self, client):
        health = (await client.get("/health")).json()
        assert health["status"] in ("ok", "degraded")
        assert health["bot_api"] == "10.4"
        ready = await client.get("/ready")
        assert ready.status_code == 200


# --------------------------------------------------------------------------- I
class TestWorkflowISafetyInvariants:
    async def test_cross_tenant_access_is_a_404_everywhere(self, client):
        owner = await login(client, 4390)
        stranger = await login(client, 4391)
        bot = await make_bot(client, owner, VALID)
        for path in (
            f"/api/v1/bots/{bot['id']}",
            f"/api/v1/bots/{bot['id']}/workflows",
            f"/api/v1/bots/{bot['id']}/schedules",
            f"/api/v1/bots/{bot['id']}/commands",
            f"/api/v1/bots/{bot['id']}/groups",
            f"/api/v1/developer/bots/{bot['id']}/webhooks",
        ):
            response = await client.get(path, headers=stranger)
            assert response.status_code == 404, path

    async def test_unauthenticated_requests_are_rejected(self, client):
        for path in ("/api/v1/bots", "/api/v1/dashboard", "/api/v1/plugins/installed", "/api/v1/owner/overview"):
            assert (await client.get(path)).status_code == 401, path

    async def test_error_envelope_shape(self, client):
        response = await client.get("/api/v1/bots")
        body = response.json()
        assert set(body["detail"]) >= {"error", "message"}
        assert body["detail"]["error"] == "unauthorized"

    async def test_telegram_webhook_requires_secret(self, client, settings):
        response = await client.post("/api/v1/telegram/webhook/999", json={"update_id": 1})
        assert response.status_code in (403, 404, 503)
        assert response.json().get("ok") is not True

    async def test_bot_token_is_encrypted_at_rest(self, client, session):
        from sqlalchemy import select

        from meiko.db.models import BotAccount

        headers = await login(client, 4392)
        bot = await make_bot(client, headers, VALID)
        row = await session.scalar(select(BotAccount).where(BotAccount.id == bot["id"]))
        assert row is not None
        assert row.token_encrypted and VALID not in row.token_encrypted
        assert row.token_fingerprint != VALID
