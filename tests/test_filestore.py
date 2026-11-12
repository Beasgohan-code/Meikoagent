"""``filestore``: quotas, visibility, collections, signed share links, dispatch."""

from __future__ import annotations

import time

import pytest

from meiko.plugins.builtin.filestore.collections import (
    MEGABYTE,
    Collection,
    StoredFile,
    Vault,
    VaultError,
)
from meiko.plugins.builtin.filestore.links import (
    LinkBook,
    issue,
    parse_start_payload,
    short_code,
    start_deep_link,
    verify,
)


def entry(index: int = 1, *, owner: int = 1, size: int = 1024, visibility: str = "private") -> StoredFile:
    return StoredFile(
        id=f"f{index}",
        name=f"file{index}.bin",
        size=size,
        owner_id=owner,
        collection="default",
        visibility=visibility,
        created_at=int(time.time()) + index,
    )


class TestQuota:
    def test_per_user_and_global_limits(self) -> None:
        vault = Vault(per_user_quota=3 * MEGABYTE, global_quota=5 * MEGABYTE, max_file_bytes=4 * MEGABYTE)
        vault.add(entry(1, size=MEGABYTE))
        vault.add(entry(2, size=MEGABYTE))
        with pytest.raises(VaultError, match="your quota"):
            vault.add(entry(3, size=2 * MEGABYTE))

    def test_single_file_limit(self) -> None:
        vault = Vault(max_file_bytes=MEGABYTE)
        with pytest.raises(VaultError, match="limit is 1 MB"):
            vault.add(entry(1, size=2 * MEGABYTE))

    def test_global_limit_across_members(self) -> None:
        vault = Vault(per_user_quota=4 * MEGABYTE, global_quota=3 * MEGABYTE, max_file_bytes=2 * MEGABYTE)
        vault.add(entry(1, owner=1, size=2 * MEGABYTE))
        with pytest.raises(VaultError, match="shared vault quota"):
            vault.add(entry(2, owner=2, size=2 * MEGABYTE))

    def test_usage_is_per_owner(self) -> None:
        vault = Vault()
        vault.add(entry(1, owner=1, size=100))
        vault.add(entry(2, owner=2, size=50))
        assert vault.used(1) == 100 and vault.used() == 150
        assert vault.count(2) == 1


class TestVisibility:
    def test_private_files_hide_from_others(self) -> None:
        vault = Vault()
        vault.add(entry(1, owner=1))
        assert vault.get("f1", user_id=1) is not None
        assert vault.get("f1", user_id=2) is None

    def test_public_files_are_visible(self) -> None:
        vault = Vault()
        vault.add(entry(1, owner=1, visibility="public"))
        assert vault.get("f1", user_id=2) is not None

    def test_lookup_by_name_and_paging(self) -> None:
        vault = Vault()
        for index in range(1, 6):
            vault.add(entry(index, owner=1))
        rows, total = vault.list_for(1, page=1, per_page=2)
        assert total == 5 and len(rows) == 2
        assert vault.get("file3.bin", user_id=1).id == "f3"

    def test_search_by_name_and_tag(self) -> None:
        vault = Vault()
        first = entry(1, owner=1)
        first.tags = ["holiday"]
        vault.add(first)
        vault.add(entry(2, owner=1))
        assert len(vault.list_for(1, query="holiday")[0]) == 1
        assert len(vault.list_for(1, query="file2")[0]) == 1

    def test_expiry_sweep(self) -> None:
        vault = Vault()
        old = entry(1, owner=1)
        old.expires_at = int(time.time()) - 10
        vault.add(old)
        vault.add(entry(2, owner=1))
        removed = vault.expiry_sweep()
        assert [f.id for f in removed] == ["f1"]
        assert vault.count() == 1


class TestCollections:
    def test_create_and_list(self) -> None:
        vault = Vault()
        col = vault.create_collection("Media", 1)
        assert col.name == "media" and vault.collections_for(1) == [col]

    def test_ownership_is_enforced(self) -> None:
        vault = Vault()
        vault.create_collection("mine", 1)
        with pytest.raises(VaultError, match="belongs to someone else"):
            vault.create_collection("mine", 2)

    def test_public_collections_are_shared(self) -> None:
        vault = Vault()
        vault.create_collection("tv", 1, public=True)
        assert len(vault.collections_for(2)) == 1

    def test_round_trip(self) -> None:
        vault = Vault()
        vault.add(entry(1, owner=1, visibility="public"))
        vault.create_collection("media", 1)
        restored = Vault.from_dict(vault.to_dict())
        assert restored.count() == 1
        assert Collection.from_dict({"name": "x", "owner_id": 1}).name == "x"


class TestShareLinks:
    def test_issue_and_verify(self) -> None:
        link = issue("FILE1", 7, "secret", ttl_days=7)
        assert verify(link, link.token, "secret") == (True, "")

    def test_wrong_token_and_secret(self) -> None:
        link = issue("FILE1", 7, "secret")
        assert verify(link, "nope", "secret")[0] is False
        assert verify(link, link.token, "other")[0] is False

    def test_expiry(self, monkeypatch) -> None:
        # the expiry is inside the signature, so travel forward in time rather
        # than editing a signed field (editing it is – correctly – "tampered")
        import meiko.plugins.builtin.filestore.links as links_module

        link = issue("FILE1", 7, "secret", ttl_days=1)
        later = time.time() + 2 * 86400
        monkeypatch.setattr(links_module.time, "time", lambda: later)
        ok, reason = verify(link, link.token, "secret")
        assert not ok and "expired" in reason

    def test_editing_a_signed_field_is_tampering(self) -> None:
        link = issue("FILE1", 7, "secret", ttl_days=1)
        link.expires_at = int(time.time()) + 10**6
        ok, reason = verify(link, link.token, "secret")
        assert not ok and "tampered" in reason

    def test_use_cap(self) -> None:
        link = issue("FILE1", 7, "secret", max_uses=1)
        link.uses = 1
        ok, reason = verify(link, link.token, "secret")
        assert not ok and "limit" in reason

    def test_deep_link_payload_round_trip(self) -> None:
        link = issue("FILE1", 7, "secret")
        url = start_deep_link("@meikobot", link.token)
        assert url.startswith("https://t.me/meikobot?start=s_")
        assert parse_start_payload(f"s_{link.token}") == link.token
        assert parse_start_payload("something_else") == ""

    def test_short_codes_avoid_look_alikes(self) -> None:
        codes = "".join(short_code(200) for _ in range(5))
        assert not set("lo01") & set(codes)

    async def test_link_book_revoke_and_consume(self) -> None:
        book = LinkBook(None, "secret")
        link = book.add(issue("FILE1", 7, "secret"))
        assert book.resolve(link.token)[0] is link
        book.consume(link)
        assert link.uses == 1
        assert book.revoke_file("FILE1") == 1
        assert book.resolve(link.token) == (None, "unknown link")

    async def test_link_book_round_trip(self) -> None:
        class Store:
            def __init__(self):
                self.data = {}

            async def get(self, key, default=None):
                return self.data.get(key, default)

            async def set(self, key, value):
                self.data[key] = value

        store = Store()
        book = await LinkBook(store, "secret").load()
        link = book.add(issue("FILE1", 7, "secret"))
        await book.save()
        reloaded = await LinkBook(store, "secret").load()
        assert reloaded.links[link.token].file_id == "FILE1"


# ---------------------------------------------------------------- dispatch
pytest.importorskip("meiko.plugins.builtin")

RUNNING: dict[str, object] = {}


class TestDispatch:
    OWNER = 880002

    @pytest.fixture()
    async def wired(self, db, cache, session):
        from aiogram import Bot, Dispatcher
        import meiko.plugins.builtin

        assert meiko.plugins.builtin.BUILTIN_MANIFESTS
        from meiko.core.commands import CommandRegistry
        from meiko.db.models import Plugin, PluginInstallation
        from meiko.plugins.base import registry
        from meiko.plugins.context import PluginScope
        from meiko.plugins.manager import PluginManager
        from meiko.services.core import BotService, UserService
        from tests.telegram_harness import Recorder

        manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
        user = await UserService(session).get_or_create_telegram_user(self.OWNER, username="fs")
        bot_row = await BotService(session).register(user.id, token="555000222:" + "F" * 34)
        recorder = Recorder()
        bot = Bot("555000222:" + "F" * 34, session=recorder)
        dispatcher = Dispatcher()
        manifest = next(m for m in registry.manifests() if m.slug == "filestore")
        row = Plugin(slug=manifest.slug, name=manifest.name, category=manifest.category)
        session.add(row)
        await session.flush()
        install = PluginInstallation(
            plugin_id=row.id, bot_id=bot_row.id, scope="bot", state="enabled",
            config={k: v.get("default") for k, v in manifest.config_schema.items() if "default" in v},
        )
        session.add(install)
        await session.flush()
        running = await manager.start(
            manifest.slug, installation=install, scope=PluginScope(bot_id=bot_row.id),
            bot=bot, dispatcher=dispatcher,
        )
        RUNNING["filestore"] = running.instance
        try:
            yield bot, dispatcher, recorder
        finally:
            await manager.stop_all()
            await bot.session.close()

    async def _send(self, wired, text: str) -> list[str]:
        from aiogram.types import Update
        from tests.telegram_harness import make_message

        bot, dispatcher, recorder = wired
        recorder.sent.clear()
        await dispatcher.feed_update(bot, Update(update_id=5, message=make_message(bot, text, self.OWNER)))
        return recorder.replies()

    async def _send_attached(self, wired, text: str, kind: str = "document") -> list[str]:
        from tests.telegram_harness import attach

        bot, dispatcher, recorder = wired
        recorder.sent.clear()
        await dispatcher.feed_update(bot, attach(bot, self.OWNER, text, kind=kind))
        return recorder.replies()

    def _plugin(self):
        return RUNNING["filestore"]

    async def test_put_stores_and_reports_quota(self, wired) -> None:
        replies = await self._send_attached(wired, "/put", kind="document")
        assert replies and "Stored" in replies[0]
        assert "archive.zip" in replies[0]
        assert self._plugin()._vault.count() == 1

    async def test_put_without_attachment_explains(self, wired) -> None:
        assert any("Nothing to store" in r for r in await self._send(wired, "/put"))

    async def test_listing_and_stats(self, wired) -> None:
        await self._send_attached(wired, "/put", kind="document")
        listing = await self._send(wired, "/myfiles")
        assert listing and "archive.zip" in listing[0]
        assert any("Vault stats" in r for r in await self._send(wired, "/vaultstats"))

    async def test_share_flow_end_to_end(self, wired) -> None:
        from meiko.plugins.builtin.filestore.links import parse_start_payload

        await self._send_attached(wired, "/put")
        file_id = next(iter(self._plugin()._vault.files))
        body = "\n".join(await self._send(wired, f"/share {file_id} 3 2"))
        assert "Share link" in body and "t.me/" in body
        token = body.split("start=s_")[1].split("<")[0].strip()
        assert parse_start_payload(f"s_{token}") == token
        assert len(self._plugin()._links.links) == 1

    async def test_share_without_id_asks_for_usage(self, wired) -> None:
        replies = await self._send(wired, "/share")
        assert any("Usage" in reply for reply in replies)

    async def test_collections_and_tags(self, wired) -> None:
        assert any("created" in r.lower() for r in await self._send(wired, "/newcollection media"))
        assert any("Collections" in r for r in await self._send(wired, "/collections"))

    async def test_search_and_usage(self, wired) -> None:
        assert any("Search" in r or "Files" in r for r in await self._send(wired, "/vaultsearch report"))
        assert any("Your vault" in r for r in await self._send(wired, "/vaultusage"))

    async def test_revoke_unknown_is_explained(self, wired) -> None:
        assert any("Not found" in r for r in await self._send(wired, "/revoke nope"))

    async def test_delete_button_removes_the_file(self, wired) -> None:
        from tests.telegram_harness import press

        await self._send_attached(wired, "/put")
        file_id = next(iter(self._plugin()._vault.files))
        bot, dispatcher, recorder = wired
        recorder.sent.clear()
        await dispatcher.feed_update(bot, press(bot, f"fs:del:{file_id}", self.OWNER))
        assert recorder.answers() and "Deleted" in recorder.answers()[0]
        assert self._plugin()._vault.count() == 0
