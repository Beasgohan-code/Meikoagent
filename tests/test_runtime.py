"""Runtime tests: worker manager, crash-loop protection, scheduler, watchdog."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from meiko.db.models import BotAccount, RuntimeEvent, ScheduledTask, User, Worker
from meiko.runtime import manager as manager_module
from meiko.runtime.manager import WorkerManager
from meiko.runtime.scheduler import Scheduler, next_run
from meiko.runtime.watchdog import Watchdog


@pytest.fixture()
def worker_manager(settings):
    manager = WorkerManager()
    manager.persist = False
    yield manager
    manager_module._manager = None


async def make_bot(session, token="555000111:" + "Q" * 34, valid=True, status="stopped"):
    user = User(telegram_id=4242, username="owner", first_name="Owner", role="owner", is_owner=True)
    session.add(user)
    await session.flush()
    bot = BotAccount(
        owner_id=user.id,
        token_encrypted="enc:v1:fake",
        token_fingerprint=token[-16:] + "x",
        name="Test",
        token_valid=valid,
        status=status,
    )
    session.add(bot)
    await session.flush()
    await session.commit()
    return bot


class TestWorkerManager:
    async def test_unverified_token_is_refused(self, db, session, worker_manager):
        bot = await make_bot(session, valid=False)
        result = await worker_manager.start(bot.id)
        assert result["status"] == "error"
        assert "verified" in result["error"]
        assert worker_manager.running_ids() == []

    async def test_missing_bot_reports_error(self, db, worker_manager):
        result = await worker_manager.start(99999)
        assert result["status"] == "error"

    async def test_capacity_is_enforced(self, db, session, worker_manager):
        worker_manager.state.capacity = 0
        bot = await make_bot(session)
        result = await worker_manager.start(bot.id)
        assert "capacity" in result["error"]

    async def test_crash_loop_protection(self, db, session, worker_manager):
        bot = await make_bot(session)
        worker_manager.state.capacity = 5
        for _ in range(settings_threshold(worker_manager)):
            worker_manager._record_crash(bot.id)
        assert worker_manager._crash_looped(bot.id)
        result = await worker_manager.start(bot.id)
        assert result["status"] == "error"
        assert "crash loop" in result["error"]

    async def test_crash_window_expires(self, db, worker_manager):
        worker_manager.state.crash_counts[7] = [0.0]  # ancient
        assert worker_manager._crash_looped(7) is False

    async def test_health_of_stopped_bot(self, db, worker_manager):
        health = await worker_manager.health(42)
        assert health["status"] == "stopped"
        assert health["plugin_count"] == 0

    async def test_register_creates_worker_row(self, db, session, worker_manager):
        worker_id = await worker_manager.register()
        assert worker_id
        worker = await session.get(Worker, worker_id)
        assert worker is not None and worker.status == "running"

    async def test_heartbeat_updates_row(self, db, session, worker_manager):
        worker_id = await worker_manager.register()
        await worker_manager.heartbeat()
        worker = await session.get(Worker, worker_id)
        assert worker.last_heartbeat_at is not None

    async def test_stop_of_unknown_bot_is_safe(self, db, worker_manager):
        assert await worker_manager.stop(1234) is False


def settings_threshold(manager: WorkerManager) -> int:
    return manager.settings.clone_crash_loop_threshold


class TestScheduler:
    async def test_cron_next_run(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        assert next_run("*/5 * * * *", base) == datetime(2026, 1, 1, 12, 5, 0)
        assert next_run(None) is None
        assert next_run("not a cron") is None

    async def test_due_task_is_executed_once(self, db, session):
        scheduler = Scheduler()
        executed = []

        async def handler(task):
            executed.append(task.id)
            return {"ok": True}

        scheduler.register("announcement", handler)
        task = ScheduledTask(
            bot_id=None,
            name="t",
            kind="announcement",
            next_run_at=datetime.utcnow() - timedelta(seconds=5),
            status="pending",
            payload={"text": "hi"},
        )
        session.add(task)
        await session.flush()
        await session.commit()
        first = await scheduler.tick()
        second = await scheduler.tick()
        assert len(first) == 1 and second == []
        assert executed == [task.id]

    async def test_failure_retries_then_fails(self, db, session):
        scheduler = Scheduler()

        async def handler(task):
            raise RuntimeError("boom")

        scheduler.register("cleanup", handler)
        task = ScheduledTask(
            bot_id=None,
            name="f",
            kind="cleanup",
            next_run_at=datetime.utcnow() - timedelta(seconds=5),
            status="pending",
            max_attempts=2,
        )
        session.add(task)
        await session.flush()
        await session.commit()
        await scheduler.execute(task.id)
        await session.refresh(task)
        assert task.status == "retrying"
        task.next_run_at = datetime.utcnow() - timedelta(seconds=1)
        await session.flush()
        await session.commit()
        await scheduler.execute(task.id)
        await session.refresh(task)
        assert task.status == "failed"
        assert scheduler.failed == 2

    async def test_missing_handler_is_reported(self, db, session):
        scheduler = Scheduler()
        task = ScheduledTask(
            bot_id=None, name="x", kind="custom", next_run_at=datetime.utcnow(), status="pending"
        )
        session.add(task)
        await session.flush()
        await session.commit()
        result = await scheduler.execute(task.id)
        assert result["ok"] is False

    async def test_cron_task_reschedules(self, db, session):
        scheduler = Scheduler()

        async def handler(task):
            return {}

        scheduler.register("cleanup", handler)
        task = ScheduledTask(
            bot_id=None,
            name="cron",
            kind="cleanup",
            cron="*/10 * * * *",
            next_run_at=datetime.utcnow() - timedelta(seconds=1),
            status="pending",
        )
        session.add(task)
        await session.flush()
        await session.commit()
        await scheduler.execute(task.id)
        await session.refresh(task)
        assert task.status == "pending"
        assert task.next_run_at > datetime.utcnow()


class TestWatchdog:
    async def test_snapshot_reports_services(self, db):
        watchdog = Watchdog(interval=5)
        snapshot = await watchdog.snapshot()
        assert snapshot["database"] == "ok"
        assert snapshot["cache"] == "ok"
        assert snapshot["healthy"] is True

    async def test_run_once_detects_dead_runtimes(self, db, session, worker_manager):
        manager_module._manager = worker_manager
        bot = await make_bot(session)

        class DeadPollingTask:
            def done(self):
                return True

            def exception(self):
                return RuntimeError("polling died")

        class DeadApp:
            _polling_task = DeadPollingTask()

            def liveness(self):
                return {"alive": False, "reasons": ["polling task is not running"]}

        async def fake_start(bot_id):
            return {"status": "error", "error": "test"}

        worker_manager.state.applications[bot.id] = DeadApp()  # type: ignore[assignment]
        worker_manager._handle_crash = fake_start  # type: ignore[method-assign]
        watchdog = Watchdog(interval=5)
        snapshot = await watchdog.run_once()
        assert bot.id in snapshot["dead_runtimes"]
        assert watchdog.recoveries

    async def test_a_wedged_runtime_is_restarted_not_ignored(self, db, session, worker_manager):
        """The task is still running but nothing has polled: crash recovery misses it."""
        manager_module._manager = worker_manager
        bot = await make_bot(session)
        restarted = []

        class RunningTask:
            def done(self):
                return False

        class WedgedApp:
            _polling_task = RunningTask()

            def liveness(self):
                return {
                    "alive": False,
                    "reasons": ["polling last ran 900s ago (limit 150s)"],
                }

            async def restart_polling(self):
                restarted.append("restarted")

        worker_manager.state.applications[bot.id] = WedgedApp()  # type: ignore[assignment]
        watchdog = Watchdog(interval=5)
        snapshot = await watchdog.run_once()
        assert snapshot["stale_runtimes"] == [bot.id]
        assert snapshot["dead_runtimes"] == []
        assert snapshot["healthy"] is False
        assert restarted == ["restarted"]
        assert any(r.get("kind") == "stale" for r in watchdog.recoveries)

    async def test_a_stale_restart_failure_falls_back_to_crash_recovery(
        self, db, session, worker_manager
    ):
        manager_module._manager = worker_manager
        bot = await make_bot(session)
        crashed = []

        class RunningTask:
            def done(self):
                return False

        class BrokenApp:
            _polling_task = RunningTask()

            def liveness(self):
                return {"alive": False, "reasons": ["stopping"]}

            async def restart_polling(self):
                raise RuntimeError("token rejected")

        async def fake_crash(bot_id):
            crashed.append(bot_id)

        worker_manager.state.applications[bot.id] = BrokenApp()  # type: ignore[assignment]
        worker_manager._handle_crash = fake_crash  # type: ignore[method-assign]
        await Watchdog(interval=5).run_once()
        assert crashed == [bot.id]

    async def test_stale_worker_marked(self, db, session):
        worker = Worker(
            name="stale-worker",
            hostname="h",
            status="running",
            last_heartbeat_at=datetime.utcnow() - timedelta(hours=1),
        )
        session.add(worker)
        await session.flush()
        await session.commit()
        watchdog = Watchdog(interval=5)
        await watchdog.run_once()
        await session.refresh(worker)
        refreshed = await session.get(Worker, worker.id)
        assert refreshed.status == "unresponsive"


class TestRetention:
    """Log tables grew forever: purge_activity existed but nothing called it."""

    async def test_old_rows_are_deleted_and_recent_ones_kept(self, db, session):
        from meiko.db.models import ActivityEvent, CommandExecution
        from meiko.services.core import run_retention

        bot = await make_bot(session)
        old = datetime.utcnow() - timedelta(days=400)
        session.add_all(
            [
                ActivityEvent(kind="old", created_at=old),
                ActivityEvent(kind="fresh"),
                CommandExecution(bot_id=bot.id, command="old", created_at=old),
                CommandExecution(bot_id=bot.id, command="fresh"),
            ]
        )
        await session.flush()
        await session.commit()

        removed = await run_retention(session)
        await session.commit()
        assert removed["activity_events"] == 1
        assert removed["command_executions"] == 1

        from sqlalchemy import select

        kinds = list((await session.scalars(select(ActivityEvent.kind))).all())
        commands = list((await session.scalars(select(CommandExecution.command))).all())
        assert kinds == ["fresh"]
        assert commands == ["fresh"]

    async def test_deletion_is_batched(self, db, session):
        from meiko.db.models import ActivityEvent
        from meiko.services.core import run_retention

        old = datetime.utcnow() - timedelta(days=400)
        session.add_all([ActivityEvent(kind="old", created_at=old) for _ in range(12)])
        await session.flush()
        await session.commit()

        first = await run_retention(session, batch_size=5)
        assert first["activity_events"] == 5, "a first run must not delete everything at once"
        await session.commit()
        second = await run_retention(session, batch_size=5)
        assert second["activity_events"] == 5

    async def test_the_watchdog_runs_retention_on_its_own(self, db, session):
        """No operator creates a cleanup task; the watchdog must do it."""
        from meiko.db.models import ActivityEvent

        old = datetime.utcnow() - timedelta(days=400)
        session.add(ActivityEvent(kind="old", created_at=old))
        await session.flush()
        await session.commit()

        watchdog = Watchdog(interval=5)
        watchdog.retention_interval = 0  # due immediately
        removed = await watchdog.run_retention_if_due()
        assert removed["activity_events"] == 1

    async def test_retention_is_not_run_on_every_tick(self, db):
        watchdog = Watchdog(interval=5)
        watchdog.retention_interval = 10_000
        first = await watchdog.run_retention_if_due()
        second = await watchdog.run_retention_if_due()
        assert first != {} or watchdog.last_retention > 0
        assert second == {}, "retention must not query the database on every tick"

    async def test_retention_can_be_disabled(self, db, monkeypatch):
        from types import SimpleNamespace

        watchdog = Watchdog(interval=5)
        watchdog.settings = SimpleNamespace(  # type: ignore[assignment]
            retention_enabled=False,
            retention_interval_seconds=0,
            retention_activity_days=90,
            retention_command_log_days=30,
            retention_runtime_event_days=30,
            retention_plugin_log_days=30,
        )
        assert await watchdog.run_retention_if_due() == {}
