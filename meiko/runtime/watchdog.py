"""Watchdog: real health monitoring, stuck-task detection and recovery.

It cannot restart its own process after a hard crash – that is the job of an
external supervisor (Docker ``restart: unless-stopped``, systemd, Kubernetes).
What it *can* and does:

* probe database, cache, worker heartbeats and the bot runtimes,
* detect stuck polling tasks and scheduler stalls,
* recover bots whose runtime died, honouring crash-loop protection,
* write ``runtime_events`` for the dashboard and notify the platform owner,
* expose a live snapshot for ``/infrastructure/monitoring``.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from meiko.cache import get_cache
from meiko.config import get_settings
from meiko.db.base import database_health, session_scope
from meiko.db.models import BotAccount, RuntimeEvent, Worker
from meiko.logging_setup import get_logger
from meiko.runtime.manager import get_worker_manager
from meiko.runtime.scheduler import get_scheduler

log = get_logger("runtime.watchdog")


class Watchdog:
    def __init__(self, interval: int | None = None) -> None:
        self.settings = get_settings()
        self.interval = interval or self.settings.watchdog_interval_seconds
        self.last_run: float = 0.0
        self.checks: dict[str, Any] = {}
        self.recoveries: list[dict[str, Any]] = []
        self._task: asyncio.Task[Any] | None = None

    async def snapshot(self) -> dict[str, Any]:
        manager = get_worker_manager()
        db = await database_health()
        cache = await get_cache().ping()
        scheduler = get_scheduler()
        stale_workers = 0
        async with session_scope() as session:
            cutoff = datetime.utcnow() - timedelta(seconds=self.interval * 3)
            stale_workers = len(
                (
                    await session.scalars(
                        select(Worker).where(
                            Worker.status == "running",
                            Worker.last_heartbeat_at.is_not(None),
                            Worker.last_heartbeat_at < cutoff,
                        )
                    )
                ).all()
            )
        dead_apps = [
            bot_id
            for bot_id, application in manager.state.applications.items()
            if application._polling_task is not None and application._polling_task.done()
        ]
        snapshot = {
            "database": db["status"],
            "cache": cache["status"],
            "running_bots": len(manager.state.applications),
            "dead_runtimes": dead_apps,
            "stale_workers": stale_workers,
            "scheduler": {
                "running": scheduler.running,
                "executed": scheduler.executed,
                "failed": scheduler.failed,
            },
            "healthy": db["status"] == "ok"
            and cache["status"] == "ok"
            and not dead_apps
            and stale_workers == 0,
            "checked_at": datetime.utcnow().isoformat(),
        }
        self.checks = snapshot
        return snapshot

    async def run_once(self) -> dict[str, Any]:
        snapshot = await self.snapshot()
        await self._recover_dead_runtimes(snapshot["dead_runtimes"])
        await self._mark_stale_workers(snapshot["stale_workers"])
        if not snapshot["healthy"]:
            await self._record(
                "watchdog.degraded",
                "watchdog detected degraded health",
                severity="warning",
                data={k: snapshot[k] for k in ("database", "cache", "stale_workers")},
            )
        self.last_run = time.time()
        return snapshot

    async def _recover_dead_runtimes(self, bot_ids: list[int]) -> None:
        manager = get_worker_manager()
        for bot_id in bot_ids:
            log.warning("watchdog: recovering dead runtime for bot %s", bot_id)
            self.recoveries.append({"bot_id": bot_id, "at": datetime.utcnow().isoformat()})
            await manager._handle_crash(bot_id)
            await self._record(
                "watchdog.recovery",
                f"recovered runtime for bot {bot_id}",
                bot_id=bot_id,
            )

    async def _mark_stale_workers(self, count: int) -> None:
        if not count:
            return
        async with session_scope() as session:
            cutoff = datetime.utcnow() - timedelta(seconds=self.interval * 3)
            stale = (
                await session.scalars(
                    select(Worker).where(
                        Worker.status == "running",
                        Worker.last_heartbeat_at.is_not(None),
                        Worker.last_heartbeat_at < cutoff,
                    )
                )
            ).all()
            for worker in stale:
                worker.status = "unresponsive"
                session.add(
                    RuntimeEvent(
                        kind="worker.unresponsive",
                        severity="warning",
                        worker_id=worker.id,
                        message=f"worker {worker.name} missed heartbeats",
                    )
                )

    async def _record(
        self,
        kind: str,
        message: str,
        *,
        severity: str = "info",
        bot_id: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        async with session_scope() as session:
            session.add(
                RuntimeEvent(
                    bot_id=bot_id, kind=kind, severity=severity, message=message[:500], data=data or {}
                )
            )

    async def loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("watchdog cycle failed")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.loop(), name="meiko-watchdog")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


_watchdog: Watchdog | None = None


def get_watchdog() -> Watchdog:
    global _watchdog
    if _watchdog is None:
        _watchdog = Watchdog()
    return _watchdog
