"""Shared module infrastructure: store, events, jobs, progress, media, ledgers."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from meiko.modules.events import EventHub
from meiko.modules.jobs import JobError, JobQueue, JobState
from meiko.modules.ledger import JobLedger, QuotaExceeded, StorageLedger
from meiko.modules.media import MediaError, MediaService, TempWorkspace, safe_name
from meiko.modules.mtproto import MTProtoAdapter, MTProtoUnavailable, capabilities
from meiko.modules.progress import ProgressRenderer, ProgressView, bar
from meiko.modules.store import ModuleStore


@pytest.fixture()
def store(tmp_path):
    return ModuleStore(tmp_path / "modules")


class TestModuleStore:
    def test_round_trip_and_defaults(self, store):
        assert store.read("music", "bot:1") == {}
        store.write("music", "bot:1", {"a": 1})
        assert store.read("music", "bot:1") == {"a": 1}

    def test_scope_names_are_filesystem_safe(self, store):
        store.write("leech", "bot:1:chat:-100", {"x": True})
        files = sorted(p.name for p in (store.root / "leech").iterdir())
        assert files == ["_scopes.json", "bot_1_chat_-100.json"]
        assert store.scopes("leech") == ["bot:1:chat:-100"]

    def test_update_merges_and_is_typed(self, store):
        store.update("encoder", "s", {"a": 1, "nested": {"x": 1}})
        merged = store.update("encoder", "s", {"b": 2})
        assert merged == {"a": 1, "nested": {"x": 1}, "b": 2}

    def test_corrupt_file_is_quarantined(self, store):
        target = store.root / "music" / "bad.json"
        target.parent.mkdir(parents=True)
        target.write_text("{not json", encoding="utf-8")
        assert store.read("music", "bad") == {}
        assert list(target.parent.glob("bad.corrupt*"))

    def test_scopes_and_invalidate(self, store):
        store.write("filestore", "bot:1", {"x": 1})
        store.write("filestore", "bot:2", {"x": 2})
        assert set(store.scopes("filestore")) == {"bot:1", "bot:2"}
        assert store.forget("filestore", "bot:1") is True
        assert store.scopes("filestore") == ["bot:2"]
        store.invalidate("filestore")
        assert store.read("filestore", "bot:2") == {"x": 2}


class TestEventHub:
    def test_subscription_filters_by_topic_and_scope(self):
        hub = EventHub()
        sub = hub.subscribe({"music"}, scope="bot:1")
        hub.publish("music.room", {"chat_id": 1}, scope="bot:1")
        hub.publish("music.room", {"chat_id": 2}, scope="bot:2")
        hub.publish("encoder.job", {}, scope="bot:1")
        assert sub.queue.qsize() == 1
        assert hub.stats()["topics"]["music.room"] == 2

    def test_slow_subscriber_drops_oldest(self):
        hub = EventHub()
        sub = hub.subscribe()
        for index in range(300):
            hub.publish("leech.progress", {"i": index})
        assert sub.dropped >= 44
        assert json.loads(json.dumps(sub.queue._queue[-1].payload))["i"] == 299

    def test_unsubscribe(self):
        hub = EventHub()
        sub = hub.subscribe()
        hub.unsubscribe(sub)
        hub.publish("x.y")
        assert hub.subscriber_count == 0


class TestJobQueue:
    async def test_retry_then_success(self):
        queue = JobQueue(max_concurrent=1, max_per_owner=1)
        attempts = {"n": 0}

        async def handler(job):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise JobError("transient")
            return {"ok": True}

        queue.register("encode", handler)
        job = await queue.execute(queue.submit("encoder", "encode", owner_id=1))
        assert job.state is JobState.DONE
        assert job.attempts == 2
        assert job.result == {"ok": True}

    async def test_non_retryable_failure_stops_immediately(self):
        queue = JobQueue()

        async def handler(job):
            raise JobError("bad input", retryable=False)

        queue.register("encode", handler)
        job = await queue.execute(queue.submit("encoder", "encode"))
        assert job.state is JobState.FAILED
        assert job.attempts == 1
        assert "bad input" in job.error

    async def test_concurrency_limits(self):
        queue = JobQueue(max_concurrent=1, max_per_owner=1)

        async def handler(job):
            await asyncio.sleep(0.05)
            return {}

        queue.register("work", handler)
        first = queue.submit("leech", "work", owner_id=7, job_id="a")
        task = asyncio.ensure_future(queue.execute(first))
        await asyncio.sleep(0.01)
        ok, reason = queue.can_accept(7)
        assert ok is False and "capacity" in reason
        await task
        assert queue.can_accept(7)[0] is True

    async def test_cancel_marks_job_cancelled(self):
        queue = JobQueue()

        async def handler(job):
            await asyncio.sleep(5)
            return {}

        queue.register("work", handler)
        job = queue.submit("leech", "work", job_id="c1")
        task = asyncio.ensure_future(queue.execute(job))
        await asyncio.sleep(0.01)
        assert queue.cancel("c1") is True
        await task
        assert job.state is JobState.CANCELLED

    async def test_cancel_respects_owner(self):
        queue = JobQueue()
        queue.register("work", lambda job: asyncio.sleep(0))
        job = queue.submit("leech", "work", owner_id=1, job_id="x")
        assert queue.cancel("x", owner_id=2) is False
        assert queue.cancel("x", owner_id=1) is True
        assert job.state is JobState.CANCELLED

    async def test_restore_marks_interrupted_jobs_failed(self):
        queue = JobQueue()
        queue.register("work", lambda job: asyncio.sleep(0))
        restored = queue.restore(
            [
                {"id": "1", "module": "encoder", "kind": "work", "state": "running"},
                {"id": "2", "module": "encoder", "kind": "work", "state": "done"},
            ]
        )
        assert restored == 2
        assert queue.get("1").state is JobState.FAILED
        assert "interrupted" in queue.get("1").error
        assert queue.get("2").state is JobState.DONE

    async def test_progress_hook_and_events(self):
        from meiko.modules.events import reset_event_hub, get_event_hub

        reset_event_hub()
        queue = JobQueue()
        seen = []

        async def handler(job):
            await job_progress(job, 50)
            return {}

        async def job_progress(job, value):
            job.progress = value

        queue.register("work", handler)
        await queue.execute(queue.submit("encoder", "work", job_id="p1"))
        topics = get_event_hub().stats()["topics"]
        assert topics["encoder.job.queued"] == 1
        assert topics["encoder.job.finished"] == 1
        assert seen == []

    async def test_stats_shape(self):
        queue = JobQueue()
        stats = queue.stats("encoder")
        for key in ("queued", "running", "done", "failed", "total", "success_rate"):
            assert key in stats


class TestLedger:
    def test_job_ledger_lifecycle_and_limit(self, store):
        ledger = JobLedger(store, "encoder", limit=1)
        entry = ledger.create(scope="bot:1", kind="encode", owner_id=7, meta={"file": "a.mkv"})
        assert entry.state == "queued"
        with pytest.raises(QuotaExceeded):
            ledger.create(scope="bot:1", kind="encode")
        ledger.update("bot:1", entry.id, progress=40.0, state="running")
        ledger.finish("bot:1", entry.id, state="done")
        rows = ledger.entries("bot:1")
        assert rows[0].state == "done"
        assert rows[0].progress == 100.0
        assert ledger.stats("bot:1")["success_rate"] == 100.0

    def test_recover_rewrites_stale_running_jobs(self, store):
        ledger = JobLedger(store, "leech")
        entry = ledger.create(scope="bot:2", kind="transfer")
        assert ledger.recover("bot:2") == 1
        assert ledger.entries("bot:2")[0].state == "failed"
        assert "interrupted" in ledger.entries("bot:2")[0].error
        assert entry.id

    def test_storage_quota_is_enforced_and_released(self, store):
        ledger = StorageLedger(store, default_limit=1000)
        usage = ledger.reserve("bot:1", 7, "filestore", 600, temporary=True)
        assert usage["used"] == 600 and usage["temp"] == 600
        with pytest.raises(QuotaExceeded):
            ledger.reserve("bot:1", 7, "leech", 500)
        usage = ledger.release("bot:1", 7, "filestore", 600, temporary=True)
        assert usage["used"] == 0
        assert ledger.reserve("bot:1", 7, "leech", 900)["used"] == 900

    def test_per_owner_limit_override(self, store):
        ledger = StorageLedger(store, default_limit=100)
        ledger.set_limit("bot:1", 7, 5000)
        assert ledger.reserve("bot:1", 7, "encoder", 4000)["used"] == 4000


class TestMedia:
    def test_blocks_executables_and_risky_types(self):
        service = MediaService(temp_root="/tmp/meiko-media-test")
        with pytest.raises(MediaError):
            service.validate("setup.exe", 100)
        with pytest.raises(MediaError):
            service.validate("clip.mp4", 100, mime="application/x-dosexec")
        with pytest.raises(MediaError):
            service.validate("empty.mkv", 0)

    def test_size_limit(self):
        service = MediaService(temp_root="/tmp/meiko-media-test", max_bytes=10)
        with pytest.raises(MediaError):
            service.validate("big.mkv", 11)
        info = service.validate("ok.mkv", 5)
        assert info.kind == "video"

    def test_safe_name_strips_traversal(self):
        assert safe_name("../../etc/passwd") == "passwd"
        assert safe_name("   ") == "file"
        assert safe_name("a" * 300).__len__() <= 120
        assert "/" not in safe_name("a/b\\c")

    def test_workspace_refuses_escape_and_cleans_up(self, tmp_path):
        workspace = TempWorkspace(tmp_path, prefix="enc")
        # traversal is neutralised in the name, and the resolved path is re-checked
        assert workspace.file("../../evil.sh").parent == workspace.path
        inside = workspace.file("clip.mp4")
        inside.write_bytes(b"x")
        assert workspace.cleanup() == 1
        assert not workspace.path.exists()

    def test_sweep_removes_expired_workspaces(self, tmp_path):
        service = MediaService(temp_root=tmp_path, ttl_seconds=60)
        workspace = service.workspace("old")
        (workspace.path / "f").write_bytes(b"x")
        assert service.sweep(now=time.time() + 3600) == 1
        assert service.usage()["files"] == 0


class TestProgress:
    def test_bar_rendering(self):
        assert bar(0) == "▱" * 10
        assert bar(100) == "▰" * 10
        assert bar(50).count("▰") == 5

    def test_view_renders_state_and_eta(self):
        view = ProgressView(title="Encoding", percent=42.5, current=100, total=200, eta=90)
        text = view.render(premium=False)
        assert "42.5%" in text and "eta 1:30" in text
        done = ProgressView(title="Encode", state="done", percent=100)
        assert "done" in done.render(premium=False)

    def test_renderer_throttles_edits(self):
        class FakeBot:
            def __init__(self):
                self.edits = 0

            async def edit_message_text(self, **kwargs):
                self.edits += 1

        class Clock:
            now = 0.0

            def __call__(self):
                return self.now

        clock = Clock()
        bot = FakeBot()
        renderer = ProgressRenderer(bot, interval=4.0, clock=clock)
        view = ProgressView(title="Leech", percent=10)

        async def run():
            assert await renderer.update(1, 10, view) is True
            assert await renderer.update(1, 10, view) is False
            clock.now = 5.0
            assert await renderer.update(1, 10, view) is True
            return bot.edits

        assert asyncio.run(run()) == 2
        assert renderer.skipped == 1

    def test_renderer_swallows_edit_failures(self):
        class BrokenBot:
            async def edit_message_text(self, **kwargs):
                raise RuntimeError("message is not modified")

        renderer = ProgressRenderer(BrokenBot(), interval=0)

        async def run():
            return await renderer.update(1, 2, ProgressView(title="x", percent=5))

        assert asyncio.run(run()) is False
        assert renderer.failures == 1


class TestMTProto:
    def test_reports_unavailable_without_dependency(self):
        caps = capabilities()
        assert caps.available is False
        assert "wzgram" in caps.reason

    def test_adapter_requires_configuration(self):
        adapter = MTProtoAdapter()
        assert adapter.available() is False
        assert adapter.health()["available"] is False
        with pytest.raises(MTProtoUnavailable):
            asyncio.run(adapter.start())

    def test_can_read_refuses_without_session(self):
        adapter = MTProtoAdapter(session_string="x", api_id=1, api_hash="y")
        allowed, reason = asyncio.run(adapter.can_read(-100))
        assert allowed is False and "not configured" in reason
