"""``linkshare`` gate verdicts and ``leech`` task engine — plus real dispatch."""

from __future__ import annotations

import time

import pytest

from meiko.plugins.builtin.leech.tasks import (
    DaemonBackend,
    LeechTask,
    LocalBackend,
    TaskManager,
    TaskState,
    guess_kind,
    parse_flags,
)
from meiko.plugins.builtin.linkshare.gate import (
    Facts,
    LinkRegistry,
    ProtectedLink,
    RequiredChat,
    Verdict,
    code_for,
    evaluate,
    token_for,
    verdict_copy,
)

CHAT_A = -1001
CHAT_B = -1002


def protected(**kwargs) -> ProtectedLink:
    base = dict(
        code="abc12345",
        url="https://t.me/private/9",
        title="Episode 1",
        created_at=int(time.time()),
        required=[RequiredChat(chat_id=CHAT_A, title="Main", invite_link="https://t.me/+inv")],
    )
    base.update(kwargs)
    return ProtectedLink(**base)


# ------------------------------------------------------------- linkshare
class TestGate:
    def test_member_gets_the_link(self) -> None:
        verdict, _ = evaluate(protected(), Facts(member_status={CHAT_A: "member"}))
        assert verdict is Verdict.ALLOW

    def test_non_member_must_join(self) -> None:
        verdict, detail = evaluate(protected(), Facts(member_status={CHAT_A: "left"}))
        assert verdict is Verdict.JOIN and "Main" in detail

    def test_request_mode_asks_for_approval(self) -> None:
        link = protected(required=[RequiredChat(chat_id=CHAT_A, title="Main", request_mode=True)])
        verdict, _ = evaluate(link, Facts(member_status={CHAT_A: "member"}))
        assert verdict is Verdict.REQUEST
        approved = evaluate(link, Facts(member_status={CHAT_A: "member"}, approved=True))
        assert approved[0] is Verdict.ALLOW

    def test_pending_beats_request(self) -> None:
        link = protected(required=[RequiredChat(chat_id=CHAT_A, request_mode=True)])
        verdict, detail = evaluate(link, Facts(member_status={CHAT_A: "left"}, pending=True))
        assert verdict is Verdict.PENDING and "waiting" in detail

    def test_restricted_user_is_blocked_not_looped(self) -> None:
        verdict, detail = evaluate(protected(), Facts(member_status={CHAT_A: "kicked"}))
        assert verdict is Verdict.BLOCKED and "restricted" in detail

    def test_expiry_claim_cap_and_disable(self) -> None:
        assert evaluate(protected(expires_at=int(time.time()) - 1), Facts())[0] is Verdict.EXPIRED
        assert evaluate(protected(max_claims=1, claims=1), Facts())[0] is Verdict.EXHAUSTED
        assert evaluate(protected(enabled=False), Facts())[0] is Verdict.DISABLED

    def test_admins_bypass_the_gate(self) -> None:
        verdict, _ = evaluate(protected(expires_at=int(time.time()) - 1), Facts(is_admin=True))
        assert verdict is Verdict.ALLOW

    def test_two_required_chats(self) -> None:
        link = protected(required=[RequiredChat(CHAT_A, "A"), RequiredChat(CHAT_B, "B")])
        verdict, detail = evaluate(link, Facts(member_status={CHAT_A: "member", CHAT_B: "left"}))
        assert verdict is Verdict.JOIN and "B" in detail

    def test_copy_never_leaks_for_denials(self) -> None:
        link = protected()
        assert verdict_copy(Verdict.JOIN, "", link).count("https://t.me/private/9") == 0
        assert verdict_copy(Verdict.ALLOW, "", link) == link.url

    def test_tokens_are_per_user(self) -> None:
        link = protected()
        assert token_for(link, 1, "s") != token_for(link, 2, "s")
        assert token_for(link, 1, "s") == token_for(link, 1, "s")
        assert len(token_for(link, 1, "s")) == 12

    def test_code_is_stable_for_a_url(self) -> None:
        assert code_for("https://t.me/x/1") == code_for("https://t.me/x/1")
        assert code_for("https://t.me/x/1") != code_for("https://t.me/x/2")

    async def test_registry_persistence_and_approvals(self) -> None:
        class Store:
            def __init__(self):
                self.data = {}

            async def get(self, key, default=None):
                return self.data.get(key, default)

            async def set(self, key, value):
                self.data[key] = value

        store = Store()
        registry = await LinkRegistry(store, "secret").load()
        registry.add(protected())
        registry.request(42)
        registry.approve(43)
        await registry.save()

        reloaded = await LinkRegistry(store, "secret").load()
        assert "abc12345" in reloaded.links
        assert 42 in reloaded.pending and 43 in reloaded.approved
        assert reloaded.decline(42) is True
        assert reloaded.decline(42) is False

    def test_round_trip(self) -> None:
        link = protected(required=[RequiredChat(CHAT_A, "Main", "https://t.me/+x", True)])
        restored = ProtectedLink.from_dict(link.to_dict())
        assert restored.required[0].request_mode is True
        assert restored.url == link.url


# ------------------------------------------------------------------ leech
class TestFlagParsing:
    def test_flags_and_urls(self) -> None:
        urls, flags = parse_flags("-m -unzip https://host/a.zip -qbit")
        assert urls == ["https://host/a.zip"]
        assert flags == {"mirror", "unzip", "qbit"}

    def test_unknown_flags_are_ignored_but_kept_out_of_urls(self) -> None:
        urls, flags = parse_flags("-nonsense https://host/a")
        assert urls == ["https://host/a"] and flags == set()

    def test_kind_guess(self) -> None:
        assert guess_kind({"mirror"}) == "mirror"
        assert guess_kind({"rclone"}) == "mirror"
        assert guess_kind({"unzip"}) == "leech"


class TestTaskModel:
    def test_progress_speed_and_eta(self) -> None:
        task = LeechTask(id="t1", url="u", user_id=1, chat_id=1)
        task.state = TaskState.ACTIVE
        task.started_at = time.time() - 10
        task.downloaded, task.total = 500, 1000
        assert 49 < task.percent < 51
        assert task.speed > 0
        assert task.eta > 0

    def test_round_trip(self) -> None:
        task = LeechTask(id="t1", url="u", user_id=1, chat_id=1, flags={"zip"})
        restored = LeechTask.from_dict(task.to_dict())
        assert restored.flags == {"zip"} and restored.state is TaskState.QUEUED

    def test_garbage_is_ignored(self) -> None:
        assert LeechTask.from_dict(None) is None


class TestManager:
    async def test_local_copy_runs_and_reports(self, tmp_path) -> None:
        source = tmp_path / "src.bin"
        source.write_bytes(b"x" * 2048)
        manager = TaskManager(LocalBackend())
        task = manager.create(str(source), 1, 1)
        seen: list[float] = []

        async def on_update(updated: LeechTask) -> None:
            seen.append(updated.percent)

        result = await manager.run(task, str(tmp_path / "dst.bin"), on_update=on_update, interval=0)
        assert result.state is TaskState.DONE
        assert (tmp_path / "dst.bin").read_bytes() == source.read_bytes()
        assert result.downloaded == 2048
        assert seen and seen[-1] == 100.0

    async def test_missing_local_path_fails_clearly(self, tmp_path) -> None:
        manager = TaskManager(LocalBackend())
        task = manager.create(str(tmp_path / "nope.bin"), 1, 1)
        result = await manager.run(task, str(tmp_path / "out.bin"))
        assert result.state is TaskState.FAILED
        assert "does not exist" in result.error

    async def test_per_user_and_global_capacity(self) -> None:
        manager = TaskManager(LocalBackend(), max_active=1, max_per_user=1)
        first = manager.create("u", 1, 1)
        assert manager.can_accept(1)[0] is True
        first.state = TaskState.ACTIVE
        ok, reason = manager.can_accept(1)
        assert not ok and "already have" in reason
        ok, reason = manager.can_accept(2)
        assert not ok and "capacity" in reason

    async def test_cancel_rules(self) -> None:
        manager = TaskManager(LocalBackend())
        task = manager.create("u", 1, 1)
        task.state = TaskState.ACTIVE
        assert manager.cancel("t", user_id=1) is False  # unknown id
        assert manager.cancel(task.id, user_id=2) is False  # not the owner
        assert manager.cancel(task.id, user_id=1) is True
        assert task.state is TaskState.CANCELLED
        assert manager.cancel(task.id, user_id=1) is False  # already stopped

    async def test_stats_and_report(self) -> None:
        manager = TaskManager(LocalBackend())
        done = manager.create("u1", 1, 1)
        done.state = TaskState.DONE
        done.downloaded = 100
        failed = manager.create("u2", 2, 1)
        failed.state = TaskState.FAILED
        stats = manager.stats()
        assert stats["done"] == 1 and stats["failed"] == 1 and stats["bytes"] == 100
        assert manager.user_report(1)["done"] == 1

    def test_daemon_backends_report_unavailable(self) -> None:
        backend = DaemonBackend("aria2", "")
        assert backend.available() is False
        assert DaemonBackend("rclone", "http://127.0.0.1:1").available() is False

    async def test_history_is_bounded(self) -> None:
        manager = TaskManager(LocalBackend(), history=3)
        for index in range(6):
            task = manager.create(f"u{index}", 1, 1)
            task.state = TaskState.DONE
            task.finished_at = index
        assert len(manager.tasks) <= 3


# ---------------------------------------------------------------- dispatch
pytest.importorskip("meiko.plugins.builtin")


async def _boot(session, cache, slug: str, token: str, owner: int):
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
    user = await UserService(session).get_or_create_telegram_user(owner, username=slug)
    bot_row = await BotService(session).register(user.id, token=token)
    recorder = Recorder()
    bot = Bot(token, session=recorder)
    dispatcher = Dispatcher()
    manifest = next(m for m in registry.manifests() if m.slug == slug)
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
    return manager, bot, dispatcher, recorder, running.instance


class TestLinkShareDispatch:
    OWNER = 880003

    @pytest.fixture()
    async def wired(self, db, cache, session):
        manager, bot, dispatcher, recorder, plugin = await _boot(
            session, cache, "linkshare", "555000333:" + "L" * 34, self.OWNER
        )
        try:
            yield bot, dispatcher, recorder, plugin
        finally:
            await manager.stop_all()
            await bot.session.close()

    async def _send(self, wired, text: str) -> list[str]:
        from aiogram.types import Update
        from tests.telegram_harness import make_message

        bot, dispatcher, recorder, _plugin = wired
        recorder.sent.clear()
        await dispatcher.feed_update(bot, Update(update_id=9, message=make_message(bot, text, self.OWNER)))
        return recorder.replies()

    async def test_protect_then_release(self, wired) -> None:
        replies = await self._send(wired, "/protect https://t.me/private/9 24 5")
        assert "Link protected" in replies[0]
        code = wired[3]._registry.links and next(iter(wired[3]._registry.links))
        link_replies = await self._send(wired, f"/getlink {code}")
        assert any("Here you go" in reply for reply in link_replies)
        assert wired[3]._registry.links[code].claims == 1

    async def test_protect_requires_a_url(self, wired) -> None:
        assert any("Usage" in r for r in await self._send(wired, "/protect nothing here"))

    async def test_require_list_mode_and_stats(self, wired) -> None:
        await self._send(wired, "/protect https://t.me/private/10")
        code = next(iter(wired[3]._registry.links))
        assert any("Requirement added" in r for r in await self._send(wired, f"/require {code} -100123 Media"))
        assert any("Protected links" in r for r in await self._send(wired, "/links"))
        assert any("Gate policy" in r for r in await self._send(wired, "/linkmode"))
        assert any("Gate policy · request" in r for r in await self._send(wired, "/linkmode request"))
        assert any("Link stats" in r for r in await self._send(wired, "/linkstats"))

    async def test_approvals_flow(self, wired) -> None:
        plugin = wired[3]
        plugin._registry.request(555)
        assert any("Pending approvals" in r for r in await self._send(wired, "/pendinglinks"))
        assert any("Approved" in r for r in await self._send(wired, "/approve 555"))
        assert 555 in plugin._registry.approved
        plugin._registry.request(556)
        assert any("Declined" in r for r in await self._send(wired, "/decline 556"))

    async def test_unprotect_removes(self, wired) -> None:
        await self._send(wired, "/protect https://t.me/private/11")
        code = next(iter(wired[3]._registry.links))
        assert any("Removed" in r for r in await self._send(wired, f"/unprotect {code}"))
        assert code not in wired[3]._registry.links


class TestLeechDispatch:
    OWNER = 880004

    @pytest.fixture()
    async def wired(self, db, cache, session):
        manager, bot, dispatcher, recorder, plugin = await _boot(
            session, cache, "leech", "555000444:" + "M" * 34, self.OWNER
        )
        try:
            yield bot, dispatcher, recorder, plugin
        finally:
            await manager.stop_all()
            await bot.session.close()

    async def _send(self, wired, text: str) -> list[str]:
        from aiogram.types import Update
        from tests.telegram_harness import make_message

        bot, dispatcher, recorder, _plugin = wired
        recorder.sent.clear()
        await dispatcher.feed_update(bot, Update(update_id=12, message=make_message(bot, text, self.OWNER)))
        return recorder.replies()

    async def test_usage_panel(self, wired) -> None:
        replies = await self._send(wired, "/leech")
        assert replies and "Mirror &amp; leech" in replies[0]

    async def test_local_leech_completes_and_uploads(self, wired, tmp_path) -> None:
        source = tmp_path / "payload.bin"
        source.write_bytes(b"z" * 4096)
        replies = await self._send(wired, f"/leech {source}")
        assert any("Task queued" in reply for reply in replies)
        # tiny local copy finishes inside one progress tick, so the final
        # on_update is what lands — the edit still reaches the user
        assert wired[2].edits() and "Task" in wired[2].edits()[-1]
        plugin = wired[3]
        task = next(iter(plugin._manager.tasks.values()))
        assert task.state is TaskState.DONE
        assert task.downloaded == 4096

    async def test_missing_path_reports_failure(self, wired, tmp_path) -> None:
        replies = await self._send(wired, f"/leech {tmp_path / 'ghost.bin'}")
        assert any("Task queued" in reply for reply in replies)
        task = next(iter(wired[3]._manager.tasks.values()))
        assert task.state is TaskState.FAILED and "does not exist" in task.error

    async def test_status_cancel_and_stats(self, wired, tmp_path) -> None:
        source = tmp_path / "a.bin"
        source.write_bytes(b"a" * 10)
        await self._send(wired, f"/leech {source}")
        task_id = next(iter(wired[3]._manager.tasks))
        assert any("State" in r for r in await self._send(wired, f"/taskstatus {task_id}"))
        assert any("Leech stats" in r for r in await self._send(wired, "/leechstats"))
        assert any("Transfer backend" in r for r in await self._send(wired, "/taskbackend"))
        assert any("Cannot cancel" in r for r in await self._send(wired, f"/cancel {task_id}"))
        assert any("Your jobs" in r for r in await self._send(wired, "/tasklist"))
