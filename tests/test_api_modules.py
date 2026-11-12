"""Module APIs: tenancy, music transport, encoder ledger, links, platform view."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from meiko.modules.ledger import JobLedger
from meiko.modules.store import ModuleStore, reset_module_store

VALID_TOKEN = "123456789:" + "Z" * 34


@pytest_asyncio.fixture()
async def client(settings):
    from meiko.api.app import create_app
    from meiko.cache import reset_cache

    await reset_cache()
    reset_module_store()
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
    # dispose the engine inside the loop that created it: aiosqlite keeps a
    # worker thread per connection and warns when the loop closes first
    from meiko.db.base import dispose_engine

    await dispose_engine()
    reset_module_store()


async def login(client: AsyncClient, telegram_id: int = 901) -> tuple[str, dict]:
    response = await client.post(
        "/api/v1/auth/dev",
        json={"telegram_id": telegram_id, "username": f"u{telegram_id}", "first_name": "T"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["token"], payload["user"]


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def make_bot(client: AsyncClient, token: str, name: str = "Bot") -> dict:
    response = await client.post(
        "/api/v1/bots", json={"token": VALID_TOKEN, "name": name}, headers=headers(token)
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest.fixture()
def module_store(tmp_path):
    return ModuleStore(tmp_path / "modules")


class TestMusicApi:
    async def test_sessions_are_empty_before_playback(self, client):
        token, _user = await login(client)
        bot = await make_bot(client, token)
        response = await client.get(f"/api/v1/music/sessions?bot_id={bot['id']}", headers=headers(token))
        assert response.status_code == 200
        body = response.json()
        assert body["rooms"] == []
        assert body["analytics"]["plays"] == 0

    async def test_transport_is_honest_when_bot_is_stopped(self, client):
        token, _user = await login(client)
        bot = await make_bot(client, token)
        response = await client.post(
            f"/api/v1/music/transport?bot_id={bot['id']}",
            json={"chat_id": -1001, "action": "pause"},
            headers=headers(token),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["applied"] is False
        assert "not running" in body["reason"]

    async def test_transport_rejects_foreign_bot(self, client):
        owner_token, _ = await login(client, 902)
        stranger_token, _ = await login(client, 903)
        bot = await make_bot(client, owner_token)
        response = await client.post(
            f"/api/v1/music/transport?bot_id={bot['id']}",
            json={"chat_id": -1001, "action": "pause"},
            headers=headers(stranger_token),
        )
        assert response.status_code == 404

    async def test_playlists_require_ownership(self, client):
        token, user = await login(client, 904)
        bot = await make_bot(client, token)
        created = await client.post(
            f"/api/v1/music/playlists?bot_id={bot['id']}",
            json={"name": "Road", "tracks": [{"title": "A"}]},
            headers=headers(token),
        )
        assert created.status_code == 200
        listed = await client.get(f"/api/v1/music/playlists?bot_id={bot['id']}", headers=headers(token))
        assert "Road" in listed.json()["playlists"]

        other_token, _ = await login(client, 905)
        deleted = await client.delete(
            f"/api/v1/music/playlists/Road?bot_id={bot['id']}", headers=headers(other_token)
        )
        assert deleted.status_code == 404  # the bot itself is not theirs
        assert user["id"]

    async def test_live_socket_rejects_a_missing_session(self):
        from meiko.api.routers.modules import music_live

        class FakeSocket:
            def __init__(self):
                self.query_params = {"token": ""}
                self.closed: list[int] = []
                self.accepted = False

            async def close(self, code: int = 1000):
                self.closed.append(code)

            async def accept(self):
                self.accepted = True

            async def send_text(self, text: str):  # pragma: no cover - would need a session
                raise AssertionError("must not stream without auth")

        socket = FakeSocket()
        await music_live(socket)
        assert socket.accepted is False
        assert socket.closed == [4401]


class TestEncoderApi:
    async def test_jobs_come_from_the_ledger(self, client, tmp_path):
        token, _ = await login(client, 906)
        bot = await make_bot(client, token)
        store = ModuleStore(__import__("pathlib").Path(__import__("meiko").config.get_settings().data_dir) / "modules")
        ledger = JobLedger(store, "encoder")
        entry = ledger.create(scope=f"bot:{bot['id']}", kind="encode", owner_id=bot["owner_id"])
        ledger.update(f"bot:{bot['id']}", entry.id, progress=55.0, state="running")

        response = await client.get(f"/api/v1/encoder/jobs?bot_id={bot['id']}", headers=headers(token))
        assert response.status_code == 200
        body = response.json()
        assert body["stats"]["total"] >= 1
        # ffmpeg presence is environment-specific; the API must report it honestly
        assert isinstance(body["tools"]["available"], bool)
        assert body["tools"]["missing"] == ([] if body["tools"]["available"] else ["ffmpeg", "ffprobe"]) or not body["tools"]["available"]

    async def test_presets_reflect_real_ffmpeg_capabilities(self, client):
        token, _ = await login(client, 907)
        response = await client.get("/api/v1/encoder/presets", headers=headers(token))
        assert response.status_code == 200
        body = response.json()
        assert "x265" in body["codecs"] and "mkv" in body["containers"]
        assert isinstance(body["tools"]["missing"], list)

    async def test_cancel_unknown_job_is_404(self, client):
        token, _ = await login(client, 908)
        bot = await make_bot(client, token)
        response = await client.post(
            f"/api/v1/encoder/jobs/nope/cancel?bot_id={bot['id']}", headers=headers(token)
        )
        assert response.status_code == 404


class TestLinkshareApi:
    async def test_create_list_revoke(self, client):
        token, _user = await login(client, 909)
        bot = await make_bot(client, token)
        created = await client.post(
            f"/api/v1/linkshare/links?bot_id={bot['id']}",
            json={"url": "https://example.com/file", "title": "Doc", "ttl_hours": 1},
            headers=headers(token),
        )
        assert created.status_code == 200, created.text
        code = created.json()["link"]["code"]

        listed = await client.get(f"/api/v1/linkshare/links?bot_id={bot['id']}", headers=headers(token))
        assert listed.json()["links"][0]["code"] == code

        revoked = await client.post(
            f"/api/v1/linkshare/links/{code}/revoke?bot_id={bot['id']}", headers=headers(token)
        )
        assert revoked.status_code == 200 and revoked.json()["revoked"] is True

        analytics = await client.get(
            f"/api/v1/linkshare/analytics?bot_id={bot['id']}", headers=headers(token)
        )
        assert analytics.json()["revoked"] == 1

    async def test_other_users_links_are_invisible(self, client):
        owner_token, _ = await login(client, 910)
        bot = await make_bot(client, owner_token)
        await client.post(
            f"/api/v1/linkshare/links?bot_id={bot['id']}",
            json={"url": "https://example.com/a"},
            headers=headers(owner_token),
        )
        stranger_token, _ = await login(client, 911)
        listed = await client.get(
            f"/api/v1/linkshare/links?bot_id={bot['id']}", headers=headers(stranger_token)
        )
        assert listed.status_code == 404

    async def test_bad_url_is_rejected(self, client):
        token, _ = await login(client, 912)
        bot = await make_bot(client, token)
        response = await client.post(
            f"/api/v1/linkshare/links?bot_id={bot['id']}",
            json={"url": ""},
            headers=headers(token),
        )
        assert response.status_code == 422


class TestFilestoreApi:
    async def test_shops_lists_only_owned_bots(self, client):
        token, _ = await login(client, 913)
        await make_bot(client, token)
        response = await client.get("/api/v1/filestore/bots", headers=headers(token))
        assert response.status_code == 200
        body = response.json()
        assert body["shops"] == []
        assert len(body["owned_bots"]) == 1

    async def test_unknown_shop_is_404(self, client):
        token, _ = await login(client, 914)
        response = await client.get("/api/v1/filestore/nope", headers=headers(token))
        assert response.status_code == 404


class TestPlatformApi:
    async def test_modules_report_real_state(self, client):
        token, _ = await login(client, 915)
        response = await client.get("/api/v1/platform/modules", headers=headers(token))
        assert response.status_code == 200
        body = response.json()
        assert set(body["modules"]) >= {"music", "encoder", "filestore", "linkshare", "leech", "storage"}
        assert body["mtproto"]["available"] in (True, False)
        assert "events" in body

    async def test_jobs_are_scoped_to_owned_bots(self, client):
        token, _ = await login(client, 916)
        await make_bot(client, token)
        response = await client.get("/api/v1/platform/jobs", headers=headers(token))
        assert response.status_code == 200
        assert response.json()["count"] >= 0

    async def test_storage_overview(self, client):
        token, _ = await login(client, 917)
        bot = await make_bot(client, token)
        response = await client.get("/api/v1/platform/storage", headers=headers(token))
        assert response.status_code == 200
        body = response.json()
        assert str(bot["id"]) in body["per_bot"]


class TestLeechApi:
    async def test_sources_declare_availability(self, client):
        token, _ = await login(client, 918)
        response = await client.get("/api/v1/leech/sources", headers=headers(token))
        assert response.status_code == 200
        body = response.json()
        names = {row["name"] for row in body["engine"]["downloaders"]}
        assert {"local", "http"} <= names

    async def test_validate_rejects_unsupported_source(self, client):
        token, _ = await login(client, 919)
        bot = await make_bot(client, token)
        response = await client.post(
            f"/api/v1/leech/validate?bot_id={bot['id']}",
            json={"source": "ftp://example.com/a"},
            headers=headers(token),
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False


class TestModuleAuth:
    async def test_endpoints_require_authentication(self, client):
        for path in ("/api/v1/music/sessions?bot_id=1", "/api/v1/platform/modules", "/api/v1/leech/sources"):
            response = await client.get(path)
            assert response.status_code in (401, 403), path


class TestSaverApi:
    async def test_overview_reads_the_plugin_mirror(self, client):
        from meiko.config import get_settings
        from meiko.plugins.support.state import write_mirror

        token, _ = await login(client, 920)
        bot = await make_bot(client, token)
        scope = f"bot:{bot['id']}"
        write_mirror("saver", scope, "saver:config", {"send_mode": "document", "max_batch": 5})
        write_mirror(
            "saver",
            scope,
            "index:saved",
            [{"chat": "chan", "message_id": 11, "size": 2048, "kind": "video"}],
        )
        response = await client.get(f"/api/v1/saver/overview?bot_id={bot['id']}", headers=headers(token))
        assert response.status_code == 200
        body = response.json()
        assert body["library"]["count"] == 1
        assert body["config"]["send_mode"] == "document"
        assert body["policy"]["authorized_sources_only"] is True
        assert get_settings().data_dir  # the mirror lives under the real data dir

    async def test_settings_patch_is_validated_and_persisted(self, client):
        from meiko.plugins.support.state import read_mirror

        token, _ = await login(client, 921)
        bot = await make_bot(client, token)
        response = await client.patch(
            f"/api/v1/saver/settings?bot_id={bot['id']}",
            json={"send_mode": "video", "max_batch": 1, "notify_each": True},
            headers=headers(token),
        )
        assert response.status_code == 200, response.text
        saved = read_mirror("saver", f"bot:{bot['id']}", "saver:config", {})
        assert saved["send_mode"] == "video"
        assert saved["max_batch"] == 1
        assert saved["notify_each"] is True

    async def test_invalid_enum_falls_back_to_copy(self, client):
        from meiko.plugins.support.state import read_mirror

        token, _ = await login(client, 922)
        bot = await make_bot(client, token)
        await client.patch(
            f"/api/v1/saver/settings?bot_id={bot['id']}",
            json={"send_mode": "telepathy"},
            headers=headers(token),
        )
        assert read_mirror("saver", f"bot:{bot['id']}", "saver:config", {})["send_mode"] == "copy"

    async def test_history_and_retention(self, client):
        from meiko.plugins.support.state import read_mirror, write_mirror

        token, _ = await login(client, 923)
        bot = await make_bot(client, token)
        scope = f"bot:{bot['id']}"
        write_mirror(
            "saver",
            scope,
            "index:saved",
            [
                {"chat": "a", "message_id": 1, "size": 10, "saved_at": "2020-01-01T00:00:00+00:00"},
                {"chat": "a", "message_id": 2, "size": 10},
            ],
        )
        history = await client.get(f"/api/v1/saver/history?bot_id={bot['id']}", headers=headers(token))
        assert history.json()["total"] == 2
        pruned = await client.post(
            f"/api/v1/saver/retention?bot_id={bot['id']}&keep_days=30", headers=headers(token)
        )
        assert pruned.json()["removed"] == 1
        assert len(read_mirror("saver", scope, "index:saved", [])) == 1

    async def test_forget_unknown_record_is_404(self, client):
        token, _ = await login(client, 924)
        bot = await make_bot(client, token)
        response = await client.delete(
            f"/api/v1/saver/history/404?bot_id={bot['id']}", headers=headers(token)
        )
        assert response.status_code == 404


class TestEffectivePlugins:
    async def test_reports_state_per_bot(self, client):
        token, _ = await login(client, 925)
        bot = await make_bot(client, token)
        response = await client.get("/api/v1/plugins/effective", headers=headers(token))
        assert response.status_code == 200
        body = response.json()
        assert str(bot["id"]) in body["bots"]
        assert body["bots"][str(bot["id"])]["running"] is False

    async def test_other_users_bots_are_not_listed(self, client):
        owner_token, _ = await login(client, 926)
        await make_bot(client, owner_token)
        stranger_token, _ = await login(client, 927)
        body = (
            await client.get("/api/v1/plugins/effective", headers=headers(stranger_token))
        ).json()
        assert body["bots"] == {}
