"""One job queue for every module (encoder, filestore, linkshare, leech, saver).

Why not six queues: concurrency limits, retries, cancellation, progress and
restart recovery are all subtle, and six copies would drift.  This engine is
deliberately small and synchronous in its bookkeeping:

* priority + FIFO within a priority,
* per-owner and global concurrency limits,
* bounded retries with recorded failures,
* cooperative cancellation (the worker checks the job flag),
* durable snapshots through a ``sink`` callback so a restart can rehydrate,
* event publication through :func:`get_event_hub` for the webapp.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


TERMINAL = {JobState.DONE, JobState.FAILED, JobState.CANCELLED}


class JobError(RuntimeError):
    """The handler failed; ``retryable`` decides whether the queue tries again."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Job:
    id: str
    module: str
    kind: str
    owner_id: int = 0
    scope: str = "global"
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 5  # lower runs first
    state: JobState = JobState.QUEUED
    attempts: int = 0
    max_attempts: int = 3
    progress: float = 0.0
    detail: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    cancel_requested: bool = False
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at) if self.started_at else 0.0

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module,
            "kind": self.kind,
            "owner_id": self.owner_id,
            "scope": self.scope,
            "payload": self.payload,
            "priority": self.priority,
            "state": self.state.value,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "progress": round(self.progress, 1),
            "detail": self.detail,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Job | None":
        if not isinstance(data, dict) or not data.get("id"):
            return None
        job = cls(
            id=str(data["id"]),
            module=str(data.get("module") or "unknown"),
            kind=str(data.get("kind") or "job"),
            owner_id=int(data.get("owner_id") or 0),
            scope=str(data.get("scope") or "global"),
            payload=dict(data.get("payload") or {}),
            priority=int(data.get("priority") or 5),
            attempts=int(data.get("attempts") or 0),
            max_attempts=int(data.get("max_attempts") or 3),
            progress=float(data.get("progress") or 0),
            detail=str(data.get("detail") or ""),
            error=str(data.get("error") or ""),
            created_at=float(data.get("created_at") or 0),
            started_at=float(data.get("started_at") or 0),
            finished_at=float(data.get("finished_at") or 0),
            result=dict(data.get("result") or {}),
        )
        job.state = JobState(str(data.get("state") or "queued"))
        return job


ProgressHook = Callable[[Job, float, str], Awaitable[None] | None]
Handler = Callable[[Job], Awaitable[dict[str, Any] | None]]


class JobQueue:
    """Runs handlers with limits, retries, cancellation and status snapshots."""

    def __init__(
        self,
        *,
        max_concurrent: int = 2,
        max_per_owner: int = 2,
        history: int = 200,
        sink: Callable[[], Awaitable[None] | None] | None = None,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.max_per_owner = max_per_owner
        self.history = history
        self.sink = sink
        self.jobs: dict[str, Job] = {}
        self.handlers: dict[str, Handler] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._order: list[str] = []
        self._event_scope: dict[str, str] = {}

    # -- registration -----------------------------------------------------
    def register(self, kind: str, handler: Handler) -> None:
        self.handlers[kind] = handler

    # -- submission -------------------------------------------------------
    def can_accept(self, owner_id: int) -> tuple[bool, str]:
        running = [job for job in self.jobs.values() if job.state is JobState.RUNNING]
        if len(running) >= self.max_concurrent:
            return False, "the worker is at capacity; retry in a moment"
        mine = [job for job in running if job.owner_id == owner_id]
        if len(mine) >= self.max_per_owner:
            return False, f"you already have {self.max_per_owner} job(s) running"
        return True, ""

    def submit(
        self,
        module: str,
        kind: str,
        *,
        owner_id: int = 0,
        scope: str = "global",
        payload: dict[str, Any] | None = None,
        priority: int = 5,
        max_attempts: int = 3,
        job_id: str | None = None,
    ) -> Job:
        if kind not in self.handlers:
            raise JobError(f"no handler registered for {kind!r}", retryable=False)
        job = Job(
            id=job_id or uuid.uuid4().hex[:10],
            module=module,
            kind=kind,
            owner_id=owner_id,
            scope=scope,
            payload=dict(payload or {}),
            priority=priority,
            max_attempts=max(1, max_attempts),
        )
        self.jobs[job.id] = job
        self._order.append(job.id)
        self._trim()
        self._publish(job, "job.queued")
        self._notify_sink()
        return job

    def restore(self, records: list[Any]) -> int:
        """Rehydrate after a restart: interrupted work becomes a failure note."""
        restored = 0
        for record in records:
            job = Job.from_dict(record)
            if job is None or job.id in self.jobs:
                continue
            if job.state in (JobState.RUNNING, JobState.QUEUED, JobState.RETRYING):
                job.state = JobState.FAILED
                job.error = "interrupted by a restart"
                job.finished_at = time.time()
            self.jobs[job.id] = job
            self._order.append(job.id)
            restored += 1
        return restored

    # -- execution --------------------------------------------------------
    async def run(self, job: Job, *, progress: ProgressHook | None = None) -> Job:
        handler = self.handlers.get(job.kind)
        if handler is None:
            job.state = JobState.FAILED
            job.error = f"no handler for {job.kind!r}"
            job.finished_at = time.time()
            return job
        job.state = JobState.RUNNING
        job.started_at = time.time()
        job.attempts += 1
        self._publish(job, "job.started")

        async def report(value: float, detail: str = "") -> None:
            job.progress = max(0.0, min(100.0, float(value)))
            job.detail = detail
            self._publish(job, "job.progress")
            if progress is not None:
                outcome = progress(job, job.progress, detail)
                if asyncio.iscoroutine(outcome):
                    await outcome

        try:
            result = await handler(job)
            if job.cancel_requested:
                raise asyncio.CancelledError
            job.progress = 100.0
            job.state = JobState.DONE
            job.result = dict(result or {})
        except asyncio.CancelledError:
            job.state = JobState.CANCELLED
            job.error = job.error or "cancelled"
        except JobError as exc:
            job.error = str(exc)
            if exc.retryable and job.attempts < job.max_attempts:
                job.state = JobState.RETRYING
            else:
                job.state = JobState.FAILED
        except Exception as exc:  # pragma: no cover - defensive
            job.error = f"{type(exc).__name__}: {exc}"
            job.state = JobState.FAILED
        finally:
            if job.state is not JobState.RETRYING:
                job.finished_at = time.time()
            self._publish(job, "job.finished")
            self._notify_sink()
            self._tasks.pop(job.id, None)
        return job

    async def execute(self, job: Job, *, progress: ProgressHook | None = None) -> Job:
        """Run one job and, when retryable, try again up to ``max_attempts``."""
        while True:
            outcome = await self.run(job, progress=progress)
            if outcome.state is not JobState.RETRYING:
                return outcome
            if job.cancel_requested:
                job.state = JobState.CANCELLED
                job.finished_at = time.time()
                return job
            self._publish(job, "job.retrying")
            job.state = JobState.QUEUED
            if job.attempts >= job.max_attempts:
                job.state = JobState.FAILED
                job.finished_at = time.time()
                return job

    def cancel(self, job_id: str, *, owner_id: int | None = None) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.terminal:
            return False
        if owner_id is not None and job.owner_id != owner_id:
            return False
        job.cancel_requested = True
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        if job.state in (JobState.QUEUED, JobState.RETRYING):
            job.state = JobState.CANCELLED
            job.finished_at = time.time()
        self._publish(job, "job.cancelled")
        self._notify_sink()
        return True

    def retry(self, job_id: str) -> Job | None:
        """Re-queue a failed job without losing its history."""
        job = self.jobs.get(job_id)
        if job is None or job.state not in (JobState.FAILED, JobState.CANCELLED):
            return None
        job.state = JobState.QUEUED
        job.error = ""
        job.progress = 0.0
        job.cancel_requested = False
        job.finished_at = 0.0
        self._publish(job, "job.queued")
        self._notify_sink()
        return job

    # -- queries ----------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list_jobs(
        self,
        *,
        module: str | None = None,
        owner_id: int | None = None,
        state: JobState | None = None,
        limit: int = 50,
    ) -> list[Job]:
        rows = [job for job in self.jobs.values() if not module or job.module == module]
        if owner_id is not None:
            rows = [job for job in rows if job.owner_id == owner_id]
        if state is not None:
            rows = [job for job in rows if job.state is state]
        rows.sort(key=lambda job: (job.priority, job.created_at))
        return rows[:limit]

    def stats(self, module: str | None = None) -> dict[str, Any]:
        rows = [job for job in self.jobs.values() if not module or job.module == module]
        counts = {state.value: 0 for state in JobState}
        for job in rows:
            counts[job.state.value] += 1
        started = [job for job in rows if job.started_at]
        durations = [job.elapsed for job in started if job.terminal]
        return {
            **counts,
            "total": len(rows),
            "avg_seconds": round(sum(durations) / len(durations), 1) if durations else 0.0,
            "success_rate": round(
                100 * counts["done"] / max(1, counts["done"] + counts["failed"]), 1
            ),
            "handlers": sorted(self.handlers),
        }

    def snapshot(self) -> list[dict[str, Any]]:
        return [job.to_dict() for job in self.jobs.values()]

    # -- internals --------------------------------------------------------
    def _trim(self) -> None:
        if len(self._order) <= self.history:
            return
        for job_id in list(self._order):
            job = self.jobs.get(job_id)
            if job is None:
                self._order.remove(job_id)
                continue
            if job.terminal and len(self._order) > self.history:
                self.jobs.pop(job_id, None)
                self._order.remove(job_id)

    def _notify_sink(self) -> None:
        if self.sink is None:
            return
        outcome = self.sink()
        if asyncio.iscoroutine(outcome):  # pragma: no cover - async sinks
            asyncio.ensure_future(outcome)

    def _publish(self, job: Job, topic: str) -> None:
        try:
            from meiko.modules.events import get_event_hub

            get_event_hub().publish(f"{job.module}.{topic}", job.to_dict(), scope=job.scope)
        except Exception:  # pragma: no cover - events must never break a job
            pass


__all__ = [
    "Handler",
    "Job",
    "JobError",
    "JobQueue",
    "JobState",
    "ProgressHook",
    "TERMINAL",
]
