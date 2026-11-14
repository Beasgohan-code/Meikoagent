"""Scheduler: cron/interval jobs with idempotency and retry bookkeeping.

Jobs live in ``scheduled_tasks``; a distributed lock guarantees a task is
executed once even when several scheduler instances run.  Failed jobs are
retried with backoff up to ``max_attempts`` and then parked as ``failed``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from croniter import CroniterBadCronError, croniter
from sqlalchemy import select

from meiko.cache import get_cache
from meiko.config import get_settings
from meiko.db.base import session_scope
from meiko.db.models import RuntimeEvent, ScheduledTask, utcnow
from meiko.logging_setup import get_logger

log = get_logger("runtime.scheduler")

JobHandler = Callable[[ScheduledTask], Awaitable[dict[str, Any]]]


def next_run(cron: str | None, base: datetime | None = None) -> datetime | None:
    if not cron:
        return None
    try:
        return croniter(cron, base or utcnow()).get_next(datetime)
    except (CroniterBadCronError, ValueError):  # pragma: no cover - guarded upstream
        log.warning("invalid cron expression %r", cron)
        return None


class Scheduler:
    """Polls due tasks and dispatches them to registered handlers."""

    def __init__(self, interval: int = 15) -> None:
        self.interval = interval
        self.handlers: dict[str, JobHandler] = {}
        self._task: asyncio.Task[Any] | None = None
        self.running = False
        self.executed = 0
        self.failed = 0

    def register(self, kind: str, handler: JobHandler) -> None:
        self.handlers[kind] = handler

    async def tick(self) -> list[dict[str, Any]]:
        """Execute every due task exactly once (guarded by a cache lock)."""
        results: list[dict[str, Any]] = []
        now = utcnow()
        async with session_scope() as session:
            due = (
                await session.scalars(
                    select(ScheduledTask)
                    .where(
                        ScheduledTask.status.in_(["pending", "retrying"]),
                        ScheduledTask.next_run_at.is_not(None),
                        ScheduledTask.next_run_at <= now,
                    )
                    .order_by(ScheduledTask.next_run_at)
                    .limit(25)
                )
            ).all()
            task_ids = [task.id for task in due]
        for task_id in task_ids:
            async with get_cache().lock(f"scheduled:{task_id}", timeout=30) as acquired:
                if not acquired:
                    continue
                results.append(await self.execute(task_id))
        return results

    async def execute(self, task_id: int) -> dict[str, Any]:
        async with session_scope() as session:
            task = await session.get(ScheduledTask, task_id)
            if task is None or task.status not in ("pending", "retrying"):
                return {"task_id": task_id, "skipped": True}
            handler = self.handlers.get(task.kind)
            payload = dict(task.payload or {})
            kind = task.kind
            task.attempts = int(task.attempts or 0) + 1
            attempts = task.attempts
            try:
                if handler is None:
                    raise RuntimeError(f"no handler registered for task kind {kind!r}")
                result = await handler(task)
                task.status = "done" if not task.cron else "pending"
                task.last_error = None
                task.last_run_at = utcnow()
                task.next_run_at = next_run(task.cron) if task.cron else None
                session.add(
                    RuntimeEvent(kind="scheduler.run", message=f"task {task.id} ({kind}) executed")
                )
                self.executed += 1
                return {"task_id": task_id, "ok": True, "result": result}
            except Exception as exc:
                self.failed += 1
                task.last_error = str(exc)[:500]
                if attempts >= int(task.max_attempts or 3):
                    task.status = "failed"
                    task.next_run_at = None
                else:
                    task.status = "retrying"
                    task.next_run_at = utcnow() + timedelta(seconds=30 * attempts)
                session.add(
                    RuntimeEvent(
                        kind="scheduler.failed",
                        severity="error",
                        message=f"task {task.id} ({kind}) failed: {exc}"[:500],
                    )
                )
                return {"task_id": task_id, "ok": False, "error": str(exc), "attempts": attempts}

    async def loop(self) -> None:
        self.running = True
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                self.running = False
                raise
            except Exception:  # pragma: no cover - resilience
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.loop(), name="meiko-scheduler")

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
        register_default_handlers(_scheduler)
    return _scheduler


async def _announcement_handler(task: ScheduledTask) -> dict[str, Any]:
    """Send an announcement through the runtime that owns the bot."""
    from meiko.runtime.manager import get_worker_manager

    payload = dict(task.payload or {})
    text = str(payload.get("text", ""))[:3500]
    chat_id = payload.get("chat_id")
    if not chat_id or not text:
        raise ValueError("announcement requires chat_id and text")
    manager = get_worker_manager()
    application = manager.state.applications.get(task.bot_id or 0)
    if application is None or application.bot is None:
        raise RuntimeError("bot is not running; announcement deferred")
    await application.bot.send_message(int(chat_id), text)
    return {"chat_id": chat_id, "length": len(text)}


async def _cleanup_handler(task: ScheduledTask) -> dict[str, Any]:
    from meiko.services.core import purge_activity

    payload = dict(task.payload or {})
    days = int(payload.get("days", 90))
    async with session_scope() as session:
        removed = await purge_activity(session, days)
    return {"purged": removed}


async def _workflow_handler(task: ScheduledTask) -> dict[str, Any]:
    from meiko.runtime.manager import get_worker_manager

    manager = get_worker_manager()
    application = manager.state.applications.get(task.bot_id or 0)
    if application is None:
        raise RuntimeError("bot is not running")
    plugin = (application.plugins._running.get(f"automation|bot:{task.bot_id}") if application.plugins else None)
    if plugin is None:
        raise RuntimeError("automation plugin is not running for this bot")
    instance = plugin.instance
    chat_id = int((task.payload or {}).get("chat_id", 0))
    executed = await instance.run_matching("scheduled", chat_id, dict(task.payload or {}))
    return {"executed": executed}


def register_default_handlers(scheduler: Scheduler) -> None:
    scheduler.register("announcement", _announcement_handler)
    scheduler.register("cleanup", _cleanup_handler)
    scheduler.register("workflow", _workflow_handler)
    scheduler.register("custom", _cleanup_handler)
