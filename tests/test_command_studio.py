"""Command Studio: registry, per-bot overrides, real execution log, dry-run tests.

The rule these tests defend: the webapp can describe, categorise, scope, enable,
search, export and *simulate* a command, but it can never point a command at new
code.  There is no endpoint that accepts ``handler_ref`` for a builtin, and the
"test" endpoint runs declaration checks only.
"""

from __future__ import annotations

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


async def login(client: AsyncClient, telegram_id: int = 6001) -> str:
    response = await client.post(
        "/api/v1/auth/dev",
        json={"telegram_id": telegram_id, "username": f"u{telegram_id}", "first_name": "T"},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def make_bot(client: AsyncClient, token: str, bot_token: str = VALID_TOKEN) -> int:
    response = await client.post(
        "/api/v1/bots", json={"token": bot_token, "name": "Studio"}, headers=headers(token)
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


class TestRegistry:
    async def test_listing_includes_builtins_with_metadata(self, client):
        token = await login(client)
        bot_id = await make_bot(client, token)
        response = await client.get(
            f"/api/v1/bots/{bot_id}/commands", headers=headers(token)
        )
        assert response.status_code == 200
        rows = response.json()
        assert rows, "the registry should not be empty"
        row = rows[0]
        for key in (
            "name",
            "description",
            "category",
            "aliases",
            "permission",
            "scope",
            "enabled",
            "owner_only",
            "plugin",
            "source",
        ):
            assert key in row, key
        # A command's handler reference is metadata, never a target the UI can set.
        assert "handler_type" in row

    async def test_search_and_category_filters(self, client):
        token = await login(client)
        bot_id = await make_bot(client, token)
        everything = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()
        target = everything[0]["name"]
        filtered = (
            await client.get(
                f"/api/v1/bots/{bot_id}/commands?search={target}", headers=headers(token)
            )
        ).json()
        assert filtered and all(target in row["name"] + " ".join(row["aliases"]) for row in filtered)
        categories = (
            await client.get(
                f"/api/v1/bots/{bot_id}/commands/categories", headers=headers(token)
            )
        ).json()
        assert sum(categories.values()) == len(everything)

    async def test_another_tenant_cannot_read_the_registry_for_my_bot(self, client):
        owner = await login(client, 6101)
        stranger = await login(client, 6102)
        bot_id = await make_bot(client, owner)
        response = await client.get(
            f"/api/v1/bots/{bot_id}/commands", headers=headers(stranger)
        )
        assert response.status_code == 404

    async def test_plugin_owned_commands_report_their_plugin(self, client):
        token = await login(client, 6103)
        bot_id = await make_bot(client, token)
        installed = await client.post(
            "/api/v1/plugins/welcome/install",
            json={"bot_id": bot_id},
            headers=headers(token),
        )
        assert installed.status_code == 201, installed.text
        rows = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()
        plugin_rows = [row for row in rows if row["plugin"]]
        assert plugin_rows, "some commands come from plugin packages"
        assert all(row["source"] == f"plugin:{row['plugin']}" for row in plugin_rows)


class TestOverrides:
    async def _first_builtin(self, client, token: str, bot_id: int) -> str:
        rows = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()
        return next(row["name"] for row in rows if row["plugin"] is None)

    async def test_override_changes_description_and_scope(self, client):
        token = await login(client, 6201)
        bot_id = await make_bot(client, token)
        name = await self._first_builtin(client, token, bot_id)
        response = await client.put(
            f"/api/v1/bots/{bot_id}/commands/{name}/override",
            json={"description": "custom help text", "scope": "group"},
            headers=headers(token),
        )
        assert response.status_code == 200, response.text
        assert response.json()["description"] == "custom help text"
        detail = (
            await client.get(
                f"/api/v1/bots/{bot_id}/commands/{name}/override", headers=headers(token)
            )
        ).json()
        assert detail["effective"]["scope"] == "group"

    async def test_override_can_disable_a_command(self, client):
        token = await login(client, 6202)
        bot_id = await make_bot(client, token)
        name = await self._first_builtin(client, token, bot_id)
        await client.put(
            f"/api/v1/bots/{bot_id}/commands/{name}/override",
            json={"enabled": False},
            headers=headers(token),
        )
        rows = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()
        row = next(r for r in rows if r["name"] == name)
        assert row["enabled"] is False

    async def test_override_cannot_repoint_a_handler(self, client):
        """``handler_ref`` must be *refused*, not silently dropped.

        Ignoring it answers 200 and leaves the client believing the command now runs
        different code. A 422 says exactly what happened.
        """
        token = await login(client, 6203)
        bot_id = await make_bot(client, token)
        name = await self._first_builtin(client, token, bot_id)
        response = await client.put(
            f"/api/v1/bots/{bot_id}/commands/{name}/override",
            json={"description": "x", "handler_ref": "os.system"},
            headers=headers(token),
        )
        assert response.status_code == 422
        assert "handler" in response.text
        detail = (
            await client.get(
                f"/api/v1/bots/{bot_id}/commands/{name}/override", headers=headers(token)
            )
        ).json()
        # Nothing was written by the refused request.
        assert detail["override"] is None or not detail["override"].get("description") == "x"
        assert "handler_ref" not in (detail["override"] or {})

    async def test_override_still_accepts_legitimate_fields(self, client):
        token = await login(client, 6207)
        bot_id = await make_bot(client, token)
        name = await self._first_builtin(client, token, bot_id)
        response = await client.put(
            f"/api/v1/bots/{bot_id}/commands/{name}/override",
            json={"description": "House rules", "category": "custom", "enabled": True},
            headers=headers(token),
        )
        assert response.status_code == 200, response.text
        assert response.json()["description"] == "House rules"

    async def test_override_rejects_alias_taken_by_another_command(self, client):
        token = await login(client, 6204)
        bot_id = await make_bot(client, token)
        rows = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()
        first, second = rows[0]["name"], rows[1]["name"]
        response = await client.put(
            f"/api/v1/bots/{bot_id}/commands/{first}/override",
            json={"aliases": [second]},
            headers=headers(token),
        )
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "alias_taken"

    async def test_override_cannot_grant_owner_level_to_a_normal_user(self, client):
        token = await login(client, 6205)
        bot_id = await make_bot(client, token)
        name = await self._first_builtin(client, token, bot_id)
        response = await client.put(
            f"/api/v1/bots/{bot_id}/commands/{name}/override",
            json={"permission": "owner"},
            headers=headers(token),
        )
        assert response.status_code == 403

    async def test_clearing_the_override_restores_defaults(self, client):
        token = await login(client, 6206)
        bot_id = await make_bot(client, token)
        name = await self._first_builtin(client, token, bot_id)
        await client.put(
            f"/api/v1/bots/{bot_id}/commands/{name}/override",
            json={"description": "temporary"},
            headers=headers(token),
        )
        cleared = await client.delete(
            f"/api/v1/bots/{bot_id}/commands/{name}/override", headers=headers(token)
        )
        assert cleared.status_code == 204
        rows = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()
        row = next(r for r in rows if r["name"] == name)
        assert row["description"] != "temporary"

    async def test_override_on_unknown_command_is_404(self, client):
        token = await login(client, 6207)
        bot_id = await make_bot(client, token)
        response = await client.put(
            f"/api/v1/bots/{bot_id}/commands/definitely_not_a_command/override",
            json={"enabled": False},
            headers=headers(token),
        )
        assert response.status_code == 404


class TestExecutionLog:
    async def test_log_is_empty_before_any_run(self, client):
        token = await login(client, 6301)
        bot_id = await make_bot(client, token)
        response = await client.get(
            f"/api/v1/bots/{bot_id}/commands/logs", headers=headers(token)
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_dry_run_is_recorded_as_studio_source(self, client):
        token = await login(client, 6302)
        bot_id = await make_bot(client, token)
        name = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()[0]["name"]
        tested = await client.post(
            f"/api/v1/bots/{bot_id}/commands/test",
            json={"name": name, "chat_type": "group", "as_role": "admin"},
            headers=headers(token),
        )
        assert tested.status_code == 200, tested.text
        logs = (
            await client.get(f"/api/v1/bots/{bot_id}/commands/logs", headers=headers(token))
        ).json()
        assert len(logs) == 1
        assert logs[0]["source"] == "studio"
        assert logs[0]["command"] == name

    async def test_stats_come_from_the_log(self, client):
        token = await login(client, 6303)
        bot_id = await make_bot(client, token)
        name = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()[0]["name"]
        for _ in range(3):
            await client.post(
                f"/api/v1/bots/{bot_id}/commands/test",
                json={"name": name},
                headers=headers(token),
            )
        stats = (
            await client.get(f"/api/v1/bots/{bot_id}/commands/stats", headers=headers(token))
        ).json()
        entry = next(row for row in stats["commands"] if row["command"] == name)
        assert entry["total"] == 3
        assert stats["source"] == "command_executions"

    async def test_middleware_records_real_invocations(self, db):
        """The bot-side middleware writes the row the Studio reads."""
        from meiko.db.base import session_scope
        from meiko.db.models import BotAccount, CommandExecution, User
        from meiko.telegram.middlewares import BotRuntimeContext
        from meiko.telegram.middlewares import CommandLogMiddleware

        async with session_scope() as session:
            user = User(telegram_id=777001, first_name="T")
            session.add(user)
            await session.flush()
            bot = BotAccount(owner_id=user.id, name="B", token_fingerprint="x" * 8)
            session.add(bot)
            await session.flush()
            bot_id = bot.id

        class FakeChat:
            id = -1001
            type = "supergroup"

        class FakeUser:
            id = 555

        class FakeMessage:
            text = "/start"
            caption = None
            chat = FakeChat()
            from_user = FakeUser()

        async def handler(event, data):
            return "ok"

        middleware = CommandLogMiddleware()
        ctx = BotRuntimeContext(bot_id=bot_id, owner_user_id=1, bot_username="b")
        await middleware(handler, FakeMessage(), {"meiko": ctx})

        async with session_scope() as session:
            rows = (await session.scalars(__import__("sqlalchemy").select(CommandExecution))).all()
        assert len(rows) == 1
        assert rows[0].command == "start"
        assert rows[0].chat_type == "supergroup"
        assert rows[0].source == "telegram"


class TestDryRun:
    async def test_scope_mismatch_is_reported_not_executed(self, client):
        token = await login(client, 6401)
        bot_id = await make_bot(client, token)
        rows = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()
        private_only = next(row for row in rows if row["scope"] == "private")
        response = await client.post(
            f"/api/v1/bots/{bot_id}/commands/test",
            json={"name": private_only["name"], "chat_type": "group", "as_role": "owner"},
            headers=headers(token),
        )
        body = response.json()
        assert body["would_run"] is False
        assert body["sent_to_telegram"] is False
        assert body["executed_handler"] is False
        scope_check = next(c for c in body["checks"] if c["check"] == "scope")
        assert scope_check["ok"] is False

    async def test_permission_check_uses_the_real_role_ladder(self, client):
        token = await login(client, 6402)
        bot_id = await make_bot(client, token)
        rows = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()
        # Make one core command require a moderator, then check the ladder.
        name = rows[0]["name"]
        raised = await client.put(
            f"/api/v1/bots/{bot_id}/commands/{name}/override",
            json={"permission": "admin"},
            headers=headers(token),
        )
        assert raised.status_code == 200, raised.text
        admin_command = next(
            row
            for row in (
                await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
            ).json()
            if row["name"] == name
        )
        assert admin_command["permission"] == "admin"
        weak = await client.post(
            f"/api/v1/bots/{bot_id}/commands/test",
            json={"name": admin_command["name"], "as_role": "everyone"},
            headers=headers(token),
        )
        assert weak.json()["would_run"] is False
        strong = await client.post(
            f"/api/v1/bots/{bot_id}/commands/test",
            json={"name": admin_command["name"], "as_role": "bot_owner"},
            headers=headers(token),
        )
        assert strong.json()["would_run"] is True

    async def test_disabled_command_never_would_run(self, client):
        token = await login(client, 6403)
        bot_id = await make_bot(client, token)
        name = (
            await client.get(f"/api/v1/bots/{bot_id}/commands", headers=headers(token))
        ).json()[0]["name"]
        await client.put(
            f"/api/v1/bots/{bot_id}/commands/{name}/override",
            json={"enabled": False},
            headers=headers(token),
        )
        body = (
            await client.post(
                f"/api/v1/bots/{bot_id}/commands/test",
                json={"name": name, "as_role": "owner"},
                headers=headers(token),
            )
        ).json()
        assert body["would_run"] is False


class TestImportExport:
    async def test_roundtrip(self, client):
        token = await login(client, 6501)
        bot_id = await make_bot(client, token)
        await client.post(
            f"/api/v1/bots/{bot_id}/commands",
            json={
                "name": "hello",
                "description": "say hi",
                "category": "general",
                "handler_type": "declarative",
                "definition": {"trigger": "hello", "response": "Hi {user}!"},
            },
            headers=headers(token),
        )
        export = await client.get(
            f"/api/v1/bots/{bot_id}/commands/export", headers=headers(token)
        )
        assert export.status_code == 200
        payload = export.json()
        assert payload["version"] == 1
        assert any(row["name"] == "hello" for row in payload["commands"])

        other = await make_bot(client, token, "987654321:" + "Y" * 34)
        imported = await client.post(
            f"/api/v1/bots/{other}/commands/import",
            json={"version": 1, "commands": payload["commands"], "replace": False},
            headers=headers(token),
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["created"] >= 1
        rows = (
            await client.get(f"/api/v1/bots/{other}/commands", headers=headers(token))
        ).json()
        assert any(row["name"] == "hello" for row in rows)

    async def test_import_rejects_an_invalid_declarative_definition(self, client):
        token = await login(client, 6502)
        bot_id = await make_bot(client, token)
        response = await client.post(
            f"/api/v1/bots/{bot_id}/commands/import",
            json={
                "version": 1,
                "commands": [
                    {
                        "name": "bad",
                        "handler_type": "declarative",
                        "definition": {"trigger": "bad", "response": "hi", "scope": "everywhere"},
                    }
                ],
            },
            headers=headers(token),
        )
        assert response.status_code == 200
        assert response.json()["skipped"], "an invalid definition must be reported, not silently stored"

    async def test_export_of_another_tenant_is_404(self, client):
        owner = await login(client, 6503)
        stranger = await login(client, 6504)
        bot_id = await make_bot(client, owner)
        response = await client.get(
            f"/api/v1/bots/{bot_id}/commands/export", headers=headers(stranger)
        )
        assert response.status_code == 404
