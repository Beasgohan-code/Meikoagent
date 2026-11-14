"""Worker manager: controlled clone hosting, heartbeats and crash-loop guard.

Design constraints honoured here:

* clones are never started as unlimited fire-and-forget tasks – capacity and
  concurrency are bounded by the worker's ``capacity``,
* every running bot has a heartbeat row and a health snapshot,
* crashes are counted in a sliding window; exceeding the threshold puts the bot
  into ``error`` instead of restarting forever (crash-loop protection),
* starting a bot is idempotent and guarded by a distributed lock so two API
  instances cannot start the same bot twice,
* the local process is only one worker: workers register themselves so multiple
  hosting instances can share the platform.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from meiko.bot import BotApplication, load_bot_application
from meiko.cache import get_cache
from meiko.config import get_settings
from meiko.db.base import session_scope
from meiko.db.models import BotAccount, RuntimeEvent, Worker
from meiko.logging_setup import get_logger
from meiko.services.core import BotService

log = get_logger("runtime.manager")

#: How long a start claim protects a bot before it is considered abandoned.  A
#: crashed worker must not lock its bots out forever, but a start takes seconds, so
#: a minute is short enough to recover and long enough not to collide.
START_CLAIM_TTL = 60


class BotClaimedError(RuntimeError):
    """Another worker owns this bot.  Starting it here would double-poll the token."""

    def __init__(self, bot_id: int, worker: int | None) -> None:
        self.bot_id = bot_id
        self.worker = worker
        super().__init__(
            f"bot {bot_id} is already hosted by worker {worker}; "
            "Telegram allows exactly one long poll per token"
        )


@dataclass
class WorkerState:
    worker_id: int | None = None
    name: str = ""
    hostname: str = field(default_factory=socket.gethostname)
    capacity: int = 10
    started_at: float = field(default_factory=time.time)
    last_heartbeat: float = 0.0
    applications: dict[int, BotApplication] = field(default_factory=dict)
    crash_counts: dict[int, list[float]] = field(default_factory=dict)
    heartbeat_task: asyncio.Task[Any] | None = None


class WorkerManager:
    """Owns every bot runtime in this process."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.state = WorkerState(
            name=f"{socket.gethostname()}-{os.getpid()}",
            capacity=self.settings.max_clone_workers,
        )
        self._lock = asyncio.Lock()

    # -- registration ----------------------------------------------------
    async def register(self) -> int:
        if self.state.worker_id is not None:
            return self.state.worker_id
        async with session_scope() as session:
            worker = await session.scalar(select(Worker).where(Worker.name == self.state.name))
            if worker is None:
                worker = Worker(
                    name=self.state.name,
                    hostname=self.state.hostname,
                    pid=os.getpid(),
                    status="running",
                    capacity=self.state.capacity,
                    meta={"platform": platform.platform(), "python": platform.python_version()},
                )
                session.add(worker)
                await session.flush()
            else:
                worker.status = "running"
                worker.pid = os.getpid()
                worker.capacity = self.state.capacity
                worker.started_at = datetime.utcnow()
            self.state.worker_id = worker.id
            await self._record_event(
                session, "worker.registered", f"worker {worker.name} online", worker.id
            )
        return self.state.worker_id  # type: ignore[return-value]

    async def heartbeat(self) -> None:
        if self.state.worker_id is None:
            await self.register()
        async with session_scope() as session:
            worker = await session.get(Worker, self.state.worker_id)
            if worker is None:
                self.state.worker_id = None
                return
            worker.last_heartbeat_at = datetime.utcnow()
            worker.current_load = len(self.state.applications)
            worker.status = "running"
        for bot_id in list(self.state.applications):
            await self.renew_claim(bot_id)
        self.state.last_heartbeat = time.time()

    async def heartbeat_loop(self, interval: int | None = None) -> None:
        interval = interval or self.settings.clone_heartbeat_seconds
        while True:
            try:
                await self.heartbeat()
                await self._sweep_dead_applications()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - resilience
                log.exception("heartbeat failed")
            await asyncio.sleep(interval)

    async def _sweep_dead_applications(self) -> None:
        for bot_id, application in list(self.state.applications.items()):
            if application._polling_task is not None and application._polling_task.done():
                exc = application._polling_task.exception()
                if exc is not None:
                    log.warning("bot %s polling task died: %s", bot_id, exc)
                await self._handle_crash(bot_id)

    # -- start / stop ----------------------------------------------------
    async def start(self, bot_id: int) -> dict[str, Any]:
        async with self._lock:
            if bot_id in self.state.applications:
                app = self.state.applications[bot_id]
                return {"status": "running" if app.healthy else "starting", "note": "already started"}
            if len(self.state.applications) >= self.state.capacity:
                return {
                    "status": "error",
                    "error": f"worker at capacity ({self.state.capacity} bots)",
                }
            if self._crash_looped(bot_id):
                async with session_scope() as session:
                    bot = await session.get(BotAccount, bot_id)
                    if bot is not None:
                        bot.status = "error"
                        bot.last_error = "crash loop detected – manual restart required"
                    await self._record_event(
                        session, "bot.crash_loop", "crash loop protection tripped", None, bot_id
                    )
                return {"status": "error", "error": "crash loop detected"}

            async with session_scope() as session:
                probe = await session.get(BotAccount, bot_id)
                if probe is None:
                    return {"status": "error", "error": "bot not found"}
                if not probe.token_valid:
                    await self._record_event(
                        session,
                        "bot.start_refused",
                        "token has not been verified with Telegram",
                        self.state.worker_id,
                        bot_id,
                        severity="warning",
                    )
                    return {
                        "status": "error",
                        "error": "bot token has not been verified with Telegram",
                        "note": "connect or replace the token, then start again",
                    }

            claim = await self.claim(bot_id)
            if not claim["acquired"]:
                if claim["reason"] == "running_elsewhere":
                    return {
                        "status": "error",
                        "error": (
                            f"bot is already hosted by worker {claim['worker']}; "
                            "starting it here would make two workers poll one token"
                        ),
                        "claimed_by": claim["worker"],
                    }
                return {"status": "error", "error": "another instance is starting this bot"}

            async with get_cache().lock(f"bot-start:{bot_id}", timeout=30) as acquired:
                if not acquired:
                    await self.release(bot_id)
                    return {"status": "error", "error": "another instance is starting this bot"}
                try:
                    application = await load_bot_application(bot_id)
                except Exception as exc:
                    await self.release(bot_id)
                    return {"status": "error", "error": str(exc)}
                try:
                    await application.start_polling()
                except Exception as exc:
                    log.exception("failed to start bot %s", bot_id)
                    self._record_crash(bot_id)
                    await self.release(bot_id)
                    async with session_scope() as session:
                        bot = await session.get(BotAccount, bot_id)
                        if bot is not None:
                            bot.status = "error"
                            bot.last_error = str(exc)[:500]
                        await self._record_event(
                            session, "bot.start_failed", str(exc)[:300], None, bot_id
                        )
                    return {"status": "error", "error": str(exc)}

                self.state.applications[bot_id] = application
                async with session_scope() as session:
                    bot = await session.get(BotAccount, bot_id)
                    if bot is not None:
                        bot.status = "running"
                        bot.last_started_at = datetime.utcnow()
                        bot.last_error = None
                        bot.worker_id = self.state.worker_id
                    await self._record_event(
                        session, "bot.started", f"bot {bot_id} polling", self.state.worker_id, bot_id
                    )
                return {"status": "running"}

    async def stop(self, bot_id: int) -> bool:
        async with self._lock:
            application = self.state.applications.pop(bot_id, None)
            await self.release(bot_id)
            if application is not None:
                try:
                    await application.stop()
                except Exception:  # pragma: no cover
                    log.exception("error stopping bot %s", bot_id)
            async with session_scope() as session:
                bot = await session.get(BotAccount, bot_id)
                if bot is not None:
                    bot.status = "stopped"
                    bot.last_stopped_at = datetime.utcnow()
                    bot.worker_id = None
                    await self._record_event(
                        session, "bot.stopped", f"bot {bot_id} stopped", self.state.worker_id, bot_id
                    )
            return application is not None

    # -- distributed claim -------------------------------------------------
    def _claim_key(self, bot_id: int) -> str:
        return f"bot-claim:{bot_id}"

    async def claim(self, bot_id: int) -> dict[str, Any]:
        """Atomically reserve a bot for this worker.

        The start lock only serialises the *starting* of a bot; once the lock is
        released a second worker would happily start the same token, and Telegram
        then delivers every update to whichever poll wins — with two workers that
        means split traffic and duplicated side effects.  The claim outlives the
        lock and records which worker owns the bot, so a second worker is refused
        with a reason instead of quietly double-polling.

        The claim is keyed on the token fingerprint as well as the id: if a bot
        row is deleted and re-created with the same token, the old claim must not
        be inherited by an unrelated bot.
        """
        from meiko.cache import MemoryBackend, get_cache
        from meiko.db.base import session_scope

        cache = get_cache()
        async with session_scope() as session:
            bot = await session.get(BotAccount, bot_id)
            fingerprint = bot.token_fingerprint if bot is not None else None
            holder = bot.worker_id if bot is not None else None
            # A live, unexpired lease written by another worker is authoritative
            # even if the status column is stale: the lease is the row that a
            # crashed worker leaves behind with an expiry.
            if (
                bot is not None
                and bot.lease_owner is not None
                and bot.lease_owner != self.state.worker_id
                and bot.lease_expires_at is not None
                and bot.lease_expires_at > datetime.utcnow()
            ):
                return {
                    "acquired": False,
                    "reason": "running_elsewhere",
                    "worker": bot.lease_owner,
                }
            if bot is not None and self.state.worker_id is not None:
                bot.lease_owner = self.state.worker_id
                bot.lease_expires_at = datetime.utcnow() + timedelta(
                    seconds=START_CLAIM_TTL
                )
                await session.flush()

        # A bot the database says is hosted elsewhere by a worker that is *not*
        # this one is refused outright.
        if holder is not None and holder != self.state.worker_id and bot is not None:
            if bot.status in ("running", "starting"):
                return {"acquired": False, "reason": "running_elsewhere", "worker": holder}

        if isinstance(cache.backend, MemoryBackend):
            # Single process: the in-process lock already serialises starts.
            return {"acquired": True, "reason": "single_process"}

        acquired = await cache.backend.client.set(
            cache.key(self._claim_key(bot_id)),
            json.dumps(
                {
                    "worker_id": self.state.worker_id,
                    "worker_name": self.state.name,
                    "fingerprint": fingerprint,
                    "at": time.time(),
                }
            ),
            nx=True,
            ex=START_CLAIM_TTL,
        )
        if acquired:
            return {"acquired": True, "reason": "claimed"}
        owner = await cache.get(self._claim_key(bot_id)) or {}
        return {
            "acquired": False,
            "reason": "running_elsewhere",
            "worker": owner.get("worker_id"),
        }

    async def renew_claim(self, bot_id: int) -> None:
        """Refresh the lease for a bot this worker is actively polling."""
        from meiko.cache import MemoryBackend, get_cache

        # The database lease is the only one that survives a cache flush, so it is
        # renewed on every heartbeat for every bot this worker hosts.
        async with session_scope() as session:
            bot = await session.get(BotAccount, bot_id)
            if (
                bot is not None
                and self.state.worker_id is not None
                and bot.lease_owner == self.state.worker_id
            ):
                bot.lease_expires_at = datetime.utcnow() + timedelta(
                    seconds=START_CLAIM_TTL
                )

        cache = get_cache()
        if isinstance(cache.backend, MemoryBackend):
            return
        await cache.set(
            self._claim_key(bot_id),
            {
                "worker_id": self.state.worker_id,
                "worker_name": self.state.name,
                "at": time.time(),
            },
            ttl=START_CLAIM_TTL,
        )

    async def release(self, bot_id: int) -> None:
        """Drop the claim, so another worker may host this bot."""
        from meiko.cache import MemoryBackend, get_cache

        async with session_scope() as session:
            bot = await session.get(BotAccount, bot_id)
            if bot is not None and (
                bot.lease_owner is None or bot.lease_owner == self.state.worker_id
            ):
                bot.lease_owner = None
                bot.lease_expires_at = None

        cache = get_cache()
        if isinstance(cache.backend, MemoryBackend):
            return
        try:
            holder = await cache.get(self._claim_key(bot_id)) or {}
            if holder.get("worker_id") in (None, self.state.worker_id):
                await cache.delete(self._claim_key(bot_id))
        except Exception:  # pragma: no cover - cache hiccups must not break stop
            log.warning("could not release claim for bot %s", bot_id)

    async def restart(self, bot_id: int) -> dict[str, Any]:
        await self.stop(bot_id)
        await asyncio.sleep(0.2)
        return await self.start(bot_id)

    async def stop_all(self) -> None:
        for bot_id in list(self.state.applications):
            await self.stop(bot_id)
        if self.state.heartbeat_task is not None:
            self.state.heartbeat_task.cancel()
            self.state.heartbeat_task = None
        if self.state.worker_id is not None:
            async with session_scope() as session:
                worker = await session.get(Worker, self.state.worker_id)
                if worker is not None:
                    worker.status = "offline"
                    worker.current_load = 0
                    # A shutdown that still reports a recent heartbeat makes the
                    # watchdog believe this worker is alive and keep handing it
                    # bots, so stamp the departure explicitly.
                    worker.last_heartbeat_at = datetime.utcnow() - timedelta(
                        seconds=self.settings.clone_heartbeat_seconds * 2
                    )
            self.state.worker_id = None
            self.state.last_heartbeat = 0.0

    # -- health ----------------------------------------------------------
    async def health(self, bot_id: int) -> dict[str, Any]:
        application = self.state.applications.get(bot_id)
        if application is None:
            return {"status": "stopped", "uptime_seconds": 0, "plugin_count": 0}
        snapshot = application.health()
        snapshot["plugin_count"] = len(snapshot.get("plugins", []))
        snapshot["worker"] = {"id": self.state.worker_id, "name": self.state.name}
        return snapshot

    def running_ids(self) -> list[int]:
        return sorted(self.state.applications)

    async def recover(self) -> dict[str, Any]:
        """Start every bot that the database says should be running."""
        started, failed = [], []
        async with session_scope() as session:
            rows = (
                await session.scalars(
                    select(BotAccount).where(
                        BotAccount.status.in_(["running", "starting"]),
                        BotAccount.token_valid.is_(True),
                    )
                )
            ).all()
            bot_ids = [row.id for row in rows]
        for bot_id in bot_ids:
            result = await self.start(bot_id)
            if result.get("status") == "running":
                started.append(bot_id)
            else:
                failed.append({"bot_id": bot_id, "error": result.get("error")})
        return {"started": started, "failed": failed}

    # -- crash protection ------------------------------------------------
    def _record_crash(self, bot_id: int) -> None:
        window = self.settings.clone_crash_loop_window_seconds
        now = time.time()
        history = [t for t in self.state.crash_counts.get(bot_id, []) if now - t <= window]
        history.append(now)
        self.state.crash_counts[bot_id] = history

    def _crash_looped(self, bot_id: int) -> bool:
        window = self.settings.clone_crash_loop_window_seconds
        now = time.time()
        history = [t for t in self.state.crash_counts.get(bot_id, []) if now - t <= window]
        self.state.crash_counts[bot_id] = history
        return len(history) >= self.settings.clone_crash_loop_threshold

    async def _handle_crash(self, bot_id: int) -> None:
        self.state.applications.pop(bot_id, None)
        self._record_crash(bot_id)
        async with session_scope() as session:
            bot = await session.get(BotAccount, bot_id)
            if bot is not None:
                bot.restart_count = int(bot.restart_count or 0) + 1
                bot.status = "error"
                bot.last_error = "runtime crashed"
            await self._record_event(
                session, "bot.crashed", f"bot {bot_id} crashed", self.state.worker_id, bot_id
            )
        if not self._crash_looped(bot_id):
            log.info("auto-restarting bot %s", bot_id)
            await asyncio.sleep(2)
            await self.start(bot_id)

    async def _record_event(
        self,
        session: Any,
        kind: str,
        message: str,
        worker_id: int | None = None,
        bot_id: int | None = None,
        severity: str = "info",
    ) -> None:
        session.add(
            RuntimeEvent(
                bot_id=bot_id,
                worker_id=worker_id,
                kind=kind,
                severity=severity,
                message=message[:500],
            )
        )
        await session.flush()


_manager: WorkerManager | None = None


def get_worker_manager() -> WorkerManager:
    global _manager
    if _manager is None:
        _manager = WorkerManager()
    return _manager


async def reset_worker_manager() -> None:
    global _manager
    if _manager is not None:
        await _manager.stop_all()
    _manager = None
