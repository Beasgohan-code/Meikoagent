"""Service layer: FileStore tenancy, Linkshare lifecycle, Leech adapters, Music."""

from __future__ import annotations

import asyncio
import time

import pytest

from meiko.modules.filestore import FileStoreService, ShopError, ShopFile
from meiko.modules.leech import (
    CloudUploader,
    HTTPDownloader,
    LocalDownloader,
    TelegramUploader,
    TransferEngine,
    TransferError,
)
from meiko.modules.links import short_code, sign_token, verify_token
from meiko.modules.linkshare import LinkError, LinkshareService
from meiko.modules.music import MusicSessions, MusicTransport
from meiko.modules.store import ModuleStore

SCOPE = "bot:1"


@pytest.fixture()
def store(tmp_path):
    return ModuleStore(tmp_path / "modules")


@pytest.fixture()
def files(store):
    return FileStoreService(store)


class TestTokens:
    def test_sign_and_verify_round_trip(self):
        token = sign_token({"a": 1}, "secret")
        assert verify_token(token, "secret") == {"a": 1}

    def test_tampering_is_detected(self):
        token = sign_token({"a": 1}, "secret")
        assert verify_token(token, "other-secret") is None
        assert verify_token(token.replace(".", ".x", 1), "secret") is None
        assert verify_token("garbage", "secret") is None

    def test_short_code_alphabet(self):
        code = short_code(12)
        assert len(code) == 12
        assert "l" not in code and "1" not in code


class TestFileStore:
    def test_register_and_duplicate_bot(self, files):
        shop = files.register(SCOPE, owner_id=10, bot_id=1, title="Shop")
        assert shop.slug and shop.title == "Shop"
        with pytest.raises(ShopError):
            files.register(SCOPE, owner_id=11, bot_id=1)

    def test_owner_isolation_and_verification(self, files):
        shop = files.register(SCOPE, owner_id=10, bot_id=1)
        assert files.verify_owner(SCOPE, shop.slug, user_id=10)["verified"] is True
        assert files.verify_owner(SCOPE, shop.slug, user_id=99)["verified"] is False
        with pytest.raises(ShopError):
            files.remove(SCOPE, shop.slug, owner_id=99)

    def test_file_listing_respects_visibility(self, files):
        shop = files.register(SCOPE, owner_id=10, bot_id=1)
        files.add_file(SCOPE, shop.slug, ShopFile(id="a", name="private.zip", size=1, visibility="private"))
        files.add_file(SCOPE, shop.slug, ShopFile(id="b", name="public.zip", size=1, visibility="public"))
        owner_rows, total = files.list_files(SCOPE, shop.slug, user_id=10)
        assert total == 2
        guest_rows, guest_total = files.list_files(SCOPE, shop.slug, user_id=42)
        assert guest_total == 1 and guest_rows[0].id == "b"

    def test_search_and_pagination(self, files):
        shop = files.register(SCOPE, owner_id=10, bot_id=1)
        for index in range(5):
            files.add_file(SCOPE, shop.slug, ShopFile(id=f"f{index}", name=f"clip{index}.mp4", size=1))
        rows, total = files.list_files(SCOPE, shop.slug, user_id=10, query="clip3")
        assert total == 1
        page1, total = files.list_files(SCOPE, shop.slug, user_id=10, page=1, per_page=2)
        assert total == 5 and len(page1) == 2

    def test_links_expire_and_enforce_use_caps(self, files, monkeypatch):
        shop = files.register(SCOPE, owner_id=10, bot_id=1)
        files.add_file(SCOPE, shop.slug, ShopFile(id="f", name="a.zip", size=1))
        link = files.issue_link(SCOPE, shop.slug, "f", secret="s", ttl_days=1, max_uses=1)
        assert link["start"].startswith("https://t.me/{bot}?start=fs_")
        entry, reason = files.redeem(SCOPE, link["token"], secret="s")
        assert entry is not None and reason == ""
        entry, reason = files.redeem(SCOPE, link["token"], secret="s")
        assert entry is None and "use limit" in reason

        expired = files.issue_link(SCOPE, shop.slug, "f", secret="s", ttl_days=1)
        monkeypatch.setattr(time, "time", lambda: time.mktime((2030, 1, 1, 0, 0, 0, 0, 0, -1)))
        entry, reason = files.redeem(SCOPE, expired["token"], secret="s")
        assert entry is None and "expired" in reason

    def test_batch_links_and_batches_registry(self, files):
        shop = files.register(SCOPE, owner_id=10, bot_id=1)
        files.add_file(SCOPE, shop.slug, ShopFile(id="f", name="a.zip", size=1))
        files.issue_link(SCOPE, shop.slug, "f", secret="s", batch="b1")
        shop = files.get(SCOPE, shop.slug)
        assert "b1" in shop.batches and shop.batches["b1"]["files"] == ["f"]

    def test_admin_and_ban_rules(self, files):
        shop = files.register(SCOPE, owner_id=10, bot_id=1)
        files.add_admin(SCOPE, shop.slug, 20, actor_id=10)
        with pytest.raises(ShopError):
            files.add_admin(SCOPE, shop.slug, 30, actor_id=20)
        files.ban(SCOPE, shop.slug, 30, actor_id=20)
        with pytest.raises(ShopError):
            files.ban(SCOPE, shop.slug, 10, actor_id=20)  # owner is immune
        shop = files.get(SCOPE, shop.slug)
        assert shop.can_manage(20) and not shop.can_manage(30)

    def test_settings_messages_and_shortener_are_scoped(self, files):
        shop = files.register(SCOPE, owner_id=10, bot_id=1)
        files.set_setting(SCOPE, shop.slug, "auto_caption", True, user_id=10)
        files.set_message(SCOPE, shop.slug, "start", "Hello {shop}", user_id=10)
        files.shortener(SCOPE, shop.slug, actor_id=10, base_url="https://s.example", api_key="supersecret")
        shop = files.get(SCOPE, shop.slug)
        assert shop.settings["auto_caption"] is True
        assert shop.message("start") == "Hello {shop}"
        assert shop.shortener["has_key"] is True
        assert "supersecret" not in str(shop.shortener)
        with pytest.raises(ShopError):
            files.set_setting(SCOPE, shop.slug, "x", 1, user_id=99)

    def test_export_omits_credentials_and_requires_owner(self, files):
        shop = files.register(SCOPE, owner_id=10, bot_id=1)
        files.shortener(SCOPE, shop.slug, actor_id=10, api_key="abc")
        document = files.export(SCOPE, shop.slug, user_id=10)
        assert "shortener" not in document
        with pytest.raises(ShopError):
            files.export(SCOPE, shop.slug, user_id=11)

    def test_force_sub_and_delete_file(self, files):
        shop = files.register(SCOPE, owner_id=10, bot_id=1)
        files.force_sub(SCOPE, shop.slug, -100, title="Channel", actor_id=10)
        assert files.get(SCOPE, shop.slug).force_sub[0]["chat_id"] == -100
        files.add_file(SCOPE, shop.slug, ShopFile(id="a", name="x.zip", size=1))
        assert files.remove_file(SCOPE, shop.slug, "a", user_id=10) is True
        with pytest.raises(ShopError):
            files.add_file(SCOPE, "missing", ShopFile(id="b", name="y", size=1))
        files.clear_force_sub(SCOPE, shop.slug, actor_id=10)
        assert files.get(SCOPE, shop.slug).force_sub == []

    def test_usage_counts_real_files(self, files):
        shop = files.register(SCOPE, owner_id=10, bot_id=1)
        files.add_file(SCOPE, shop.slug, ShopFile(id="a", name="x", size=500))
        assert files.get(SCOPE, shop.slug).usage()["bytes"] == 500


class TestLinkshare:
    def test_lifecycle_states(self, store):
        service = LinkshareService(store, "test-secret-key-16chars")
        link = service.create(SCOPE, url="https://x/y", owner_id=5, ttl_hours=1, max_claims=1)
        assert link.state == "active"
        claimed, reason = service.claim(SCOPE, link.code)
        assert claimed is not None and reason == ""
        assert service.get(SCOPE, link.code).state == "exhausted"
        claimed, reason = service.claim(SCOPE, link.code)
        assert claimed is None and "exhausted" in reason
        assert service.analytics(SCOPE)["refusals"] == 1

    def test_revoke_requires_owner(self, store):
        service = LinkshareService(store, "test-secret-key-16chars")
        link = service.create(SCOPE, url="https://x/y", owner_id=5, ttl_hours=0)
        with pytest.raises(LinkError):
            service.revoke(SCOPE, link.code, owner_id=6)
        assert service.revoke(SCOPE, link.code, owner_id=5) is True
        assert service.get(SCOPE, link.code).state == "revoked"

    def test_expiry(self, store, monkeypatch):
        service = LinkshareService(store, "test-secret-key-16chars")
        link = service.create(SCOPE, url="https://x/y", owner_id=5, ttl_hours=1)
        monkeypatch.setattr(time, "time", lambda: time.mktime((2030, 1, 1, 0, 0, 0, 0, 0, -1)))
        claimed, reason = service.claim(SCOPE, link.code)
        assert claimed is None and "expired" in reason

    def test_batch_creation_and_revocation(self, store):
        service = LinkshareService(store, "test-secret-key-16chars")
        batch, links = service.create_batch(SCOPE, urls=["https://a", "https://b"], owner_id=5)
        assert batch and len(links) == 2
        assert service.revoke_batch(SCOPE, batch, owner_id=5) == 2
        assert service.analytics(SCOPE)["revoked"] == 2
        with pytest.raises(LinkError):
            service.revoke_batch(SCOPE, batch, owner_id=6)

    def test_analytics_is_honest_about_telemetry(self, store):
        service = LinkshareService(store, "test-secret-key-16chars")
        payload = service.analytics(SCOPE)
        assert "Telegram does not report" in payload["note"]

    def test_short_token_round_trip(self, store):
        service = LinkshareService(store, "test-secret-key-16chars")
        link = service.create(SCOPE, url="https://a", owner_id=5)
        token = service.short_token(SCOPE, link.code)
        assert service.resolve_token(token)["code"] == link.code
        assert service.resolve_token("nope") is None

    def test_unknown_scope_is_empty(self, store):
        service = LinkshareService(store, "test-secret-key-16chars")
        assert service.list_links("bot:404") == []


class TestLeechEngine:
    def test_local_download_copies_and_hashes(self, tmp_path):
        source = tmp_path / "in.bin"
        source.write_bytes(b"payload" * 100)
        engine = TransferEngine()
        result = asyncio.run(engine.fetch(str(source), tmp_path / "out.bin"))
        assert result.ok and result.bytes == 700 and result.sha256
        assert (tmp_path / "out.bin").read_bytes() == source.read_bytes()

    def test_size_limit_is_enforced_before_copy(self, tmp_path):
        source = tmp_path / "in.bin"
        source.write_bytes(b"x" * 100)
        engine = TransferEngine()
        with pytest.raises(TransferError) as caught:
            asyncio.run(engine.fetch(str(source), tmp_path / "out.bin", max_bytes=10))
        assert caught.value.retryable is False

    def test_unknown_scheme_is_rejected_honestly(self, tmp_path):
        engine = TransferEngine()
        with pytest.raises(TransferError) as caught:
            asyncio.run(engine.fetch("ftp://example.com/x", tmp_path / "x"))
        assert "no downloader" in str(caught.value)

    def test_supports_reports_truthfully(self):
        assert HTTPDownloader.supports("https://example.com/a")
        assert not HTTPDownloader.supports("ftp://example.com/a")
        assert LocalDownloader.supports("file:///tmp/a")
        assert not LocalDownloader.supports("https://example.com/a")

    def test_unavailable_destinations_are_declared(self):
        assert TelegramUploader().available() is False
        assert CloudUploader().available() is False
        engine = TransferEngine(uploaders=[TelegramUploader(), CloudUploader()])
        assert engine.uploader_for("-100") is None
        described = engine.describe()
        assert described["uploaders"] == [
            {"name": "telegram", "available": False},
            {"name": "cloud", "available": False},
        ]

    def test_push_without_uploader_refuses(self, tmp_path):
        target = tmp_path / "f.bin"
        target.write_bytes(b"x")
        with pytest.raises(TransferError):
            asyncio.run(TransferEngine().push(target, "-100"))

    def test_retries_then_fails(self, tmp_path):
        class Flaky(LocalDownloader):
            attempts = 0

            async def download(self, source, destination, progress=None):
                Flaky.attempts += 1
                raise TransferError("boom")

        source = tmp_path / "in.bin"
        source.write_bytes(b"x")
        engine = TransferEngine([Flaky()], max_attempts=3, retry_delay=0)
        with pytest.raises(TransferError):
            asyncio.run(engine.fetch(str(source), tmp_path / "out"))
        assert Flaky.attempts == 3


class TestMusic:
    def test_sessions_publish_and_count_plays_once(self, store):
        sessions = MusicSessions(store)
        sessions.publish_room(1, -100, {"current": {"title": "A", "duration": 120}, "items": []})
        sessions.publish_room(1, -100, {"current": {"title": "A", "duration": 120}, "items": []})
        analytics = sessions.analytics(1)
        assert analytics["plays"] == 1  # the same track is not double counted
        sessions.publish_room(1, -100, {"current": {"title": "B", "duration": 60}, "items": []})
        assert sessions.analytics(1)["plays"] == 2

    def test_rooms_are_isolated_per_chat(self, store):
        sessions = MusicSessions(store)
        sessions.publish_room(1, -100, {"current": {"title": "A"}, "items": []})
        sessions.publish_room(1, -200, {"current": {"title": "B"}, "items": []})
        assert sessions.room(1, -100)["current"]["title"] == "A"
        assert sessions.room(1, -200)["current"]["title"] == "B"
        assert len(sessions.active_rooms(1)) == 2

    def test_playlists_are_owner_scoped(self, store):
        sessions = MusicSessions(store)
        sessions.save_playlist(1, "Mix", [{"title": "x"}], owner_id=5)
        assert "Mix" in sessions.playlists(1, owner_id=5)
        assert sessions.playlists(1, owner_id=6) == {}
        with pytest.raises(PermissionError):
            sessions.delete_playlist(1, "Mix", owner_id=6)
        assert sessions.delete_playlist(1, "Mix", owner_id=5) is True

    def test_preferences_and_presence(self, store):
        sessions = MusicSessions(store)
        sessions.set_preference(1, 5, "volume", 70)
        assert sessions.preferences(1, 5)["volume"] == 70
        sessions.touch_listener(1, -100, 5, "Ada")
        assert sessions.listeners(1, -100)[0]["name"] == "Ada"

    def test_transport_reports_bot_not_running(self, store):
        transport = MusicTransport(MusicSessions(store))
        result = asyncio.run(transport.dispatch(999, -100, "pause"))
        assert result["applied"] is False
        assert "not running" in result["reason"]

    def test_transport_rejects_unknown_action(self, store):
        transport = MusicTransport(MusicSessions(store))
        result = asyncio.run(transport.dispatch(1, -100, "explode"))
        assert result["applied"] is False


class TestClaimTokensCannotBeForged:
    """Claim tokens were signed with the literal string "meiko-linkshare".

    That string is in the source, so with ENCRYPTION_KEY unset anyone could HMAC
    their own token for any code and collect a link without being a member of the
    required chats.  The fallback is now the generated key file and a public
    constant is refused outright.
    """

    def test_a_short_or_public_secret_is_refused(self):
        import pytest

        from meiko.modules.linkshare import LinkshareService

        for secret in ("", "meiko-linkshare", "short"):
            with pytest.raises(ValueError, match="signing secret"):
                LinkshareService(object(), secret)

    def test_the_router_never_falls_back_to_the_public_constant(self, tmp_path, monkeypatch):
        from meiko.api.routers import modules as modules_router

        monkeypatch.setenv("ENCRYPTION_KEY", "")
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        from meiko.config import get_settings

        get_settings.cache_clear()
        try:
            secret = modules_router.linkshare_signing_secret()
        finally:
            get_settings.cache_clear()
        assert secret != "meiko-linkshare"
        assert len(secret) >= 16
        # stable across calls, so existing links keep verifying after a restart
        assert modules_router.linkshare_signing_secret() == secret

    def test_a_configured_key_is_used_when_present(self, monkeypatch):
        from meiko.api.routers import modules as modules_router
        from meiko.config import get_settings

        monkeypatch.setenv("ENCRYPTION_KEY", "x" * 44)
        get_settings.cache_clear()
        try:
            assert modules_router.linkshare_signing_secret() == "x" * 44
        finally:
            get_settings.cache_clear()
