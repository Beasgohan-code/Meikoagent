"""Durable job ledger + storage ledger for the dashboards.

:class:`~meiko.modules.jobs.JobQueue` owns *execution*; this ledger owns the
*record*.  Modules that already have their own async task (the encoder drives
ffmpeg directly, leech keeps a ``TaskManager``) still need history that survives
a restart and a number the webapp can show, so they write here.

The storage ledger answers the other half of the spec: per-user / per-bot /
per-module quotas with ownership checks, temp vs persistent bytes and retention.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from meiko.modules.events import get_event_hub

MAX_RECORDS = 500
STATES = {"queued", "running", "done", "failed", "cancelled"}


class QuotaExceeded(RuntimeError):
    """The caller asked for more space than its quota allows."""


def human_bytes(value: int | float) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


@dataclass
class LedgerEntry:
    id: str
    module: str
    kind: str
    owner_id: int = 0
    scope: str = "global"
    state: str = "queued"
    progress: float = 0.0
    detail: str = ""
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    @property
    def seconds(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, end - self.created_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module,
            "kind": self.kind,
            "owner_id": self.owner_id,
            "scope": self.scope,
            "state": self.state,
            "progress": round(self.progress, 1),
            "detail": self.detail,
            "error": self.error,
            "meta": self.meta,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "seconds": round(self.seconds, 1),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "LedgerEntry | None":
        if not isinstance(data, dict) or not data.get("id"):
            return None
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        payload.setdefault("module", "unknown")
        payload.setdefault("kind", "job")
        return cls(**payload)


class JobLedger:
    """Records job lifecycle for one module, in the shared module store."""

    def __init__(self, store: Any, module: str, *, limit: int = 10) -> None:
        self.store = store
        self.module = module
        self.limit = limit  # in-flight jobs per scope

    # -- storage ----------------------------------------------------------
    def _key(self, scope: str) -> str:
        return f"ledger:{scope}"

    def _load(self, scope: str) -> list[LedgerEntry]:
        document = self.store.read(self.module, self._key(scope))
        rows = []
        for item in document.get("jobs") or []:
            entry = LedgerEntry.from_dict(item)
            if entry is not None:
                rows.append(entry)
        return rows

    def _save(self, scope: str, rows: Iterable[LedgerEntry]) -> None:
        ordered = sorted(rows, key=lambda entry: entry.created_at)[-MAX_RECORDS:]
        self.store.write(self.module, self._key(scope), {"jobs": [entry.to_dict() for entry in ordered]})

    # -- lifecycle --------------------------------------------------------
    def create(
        self,
        *,
        scope: str,
        kind: str,
        owner_id: int = 0,
        meta: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        rows = self._load(scope)
        running = [entry for entry in rows if entry.state in {"queued", "running"}]
        if len(running) >= self.limit:
            raise QuotaExceeded(f"this scope already has {self.limit} active {self.module} job(s)")
        entry = LedgerEntry(
            id=uuid.uuid4().hex[:10],
            module=self.module,
            kind=kind,
            owner_id=owner_id,
            scope=scope,
            meta=dict(meta or {}),
        )
        rows.append(entry)
        self._save(scope, rows)
        self._publish(entry, f"{self.module}.job.created")
        return entry

    def update(self, scope: str, entry_id: str, **fields: Any) -> LedgerEntry | None:
        rows = self._load(scope)
        for entry in rows:
            if entry.id != entry_id:
                continue
            for key, value in fields.items():
                if hasattr(entry, key) and value is not None:
                    setattr(entry, key, value)
            if fields.get("state") in {"done", "failed", "cancelled"}:
                entry.finished_at = time.time()
            entry.updated_at = time.time()
            self._save(scope, rows)
            self._publish(entry, f"{self.module}.job.updated")
            return entry
        return None

    def finish(
        self,
        scope: str,
        entry_id: str,
        *,
        state: str = "done",
        error: str = "",
        detail: str = "",
    ) -> LedgerEntry | None:
        if state not in {"done", "failed", "cancelled"}:
            state = "done"
        return self.update(
            scope,
            entry_id,
            state=state,
            error=error,
            detail=detail or None,
            progress=100.0 if state == "done" else None,
        )

    # -- reads ------------------------------------------------------------
    def entries(self, scope: str, *, limit: int = 50, state: str = "") -> list[LedgerEntry]:
        rows = self._load(scope)
        if state:
            rows = [entry for entry in rows if entry.state == state]
        rows.sort(key=lambda entry: entry.created_at, reverse=True)
        return rows[:limit]

    def active(self, scope: str) -> list[LedgerEntry]:
        return [entry for entry in self._load(scope) if entry.state in {"queued", "running"}]

    def stats(self, scope: str) -> dict[str, Any]:
        rows = self._load(scope)
        counts = {state: 0 for state in STATES}
        for entry in rows:
            counts[entry.state] = counts.get(entry.state, 0) + 1
        finished = [entry.seconds for entry in rows if entry.state == "done"]
        return {
            **counts,
            "total": len(rows),
            "avg_seconds": round(sum(finished) / len(finished), 1) if finished else 0.0,
            "success_rate": round(100 * counts["done"] / max(1, counts["done"] + counts["failed"]), 1),
        }

    def recover(self, scope: str) -> int:
        """Mark jobs left running by a crash as failed, with an honest reason."""
        rows = self._load(scope)
        changed = 0
        for entry in rows:
            if entry.state in {"queued", "running"}:
                entry.state = "failed"
                entry.error = entry.error or "interrupted by a restart"
                entry.finished_at = time.time()
                changed += 1
        if changed:
            self._save(scope, rows)
        return changed

    def _publish(self, entry: LedgerEntry, topic: str) -> None:
        try:
            get_event_hub().publish(topic, entry.to_dict(), scope=entry.scope)
        except Exception:  # pragma: no cover
            pass


class StorageLedger:
    """Per-user / per-bot / per-module byte accounting with quotas."""

    MODULE = "storage"

    def __init__(self, store: Any, *, default_limit: int = 2 * 1024 * 1024 * 1024) -> None:
        self.store = store
        self.default_limit = default_limit

    def _load(self, scope: str) -> dict[str, Any]:
        document = self.store.read(self.MODULE, f"usage:{scope}")
        document.setdefault("owners", {})
        document.setdefault("limits", {})
        return document

    def _save(self, scope: str, document: dict[str, Any]) -> None:
        self.store.write(self.MODULE, f"usage:{scope}", document)

    def limit(self, scope: str, owner_id: int) -> int:
        document = self._load(scope)
        return int(document["limits"].get(str(owner_id), self.default_limit))

    def set_limit(self, scope: str, owner_id: int, limit_bytes: int) -> int:
        document = self._load(scope)
        document["limits"][str(owner_id)] = max(0, int(limit_bytes))
        self._save(scope, document)
        return document["limits"][str(owner_id)]

    def usage(self, scope: str, owner_id: int) -> dict[str, Any]:
        document = self._load(scope)
        row = document["owners"].get(str(owner_id)) or {}
        modules = {key: int(value) for key, value in (row.get("modules") or {}).items()}
        used = sum(modules.values())
        limit = self.limit(scope, owner_id)
        return {
            "owner_id": owner_id,
            "used": used,
            "used_human": human_bytes(used),
            "limit": limit,
            "limit_human": human_bytes(limit),
            "percent": round(100 * used / limit, 1) if limit else 0.0,
            "modules": {key: {"bytes": value, "human": human_bytes(value)} for key, value in modules.items()},
            "persistent": int(row.get("persistent") or 0),
            "temp": int(row.get("temp") or 0),
        }

    def reserve(
        self,
        scope: str,
        owner_id: int,
        module: str,
        nbytes: int,
        *,
        temporary: bool = False,
        limit_bytes: int | None = None,
    ) -> dict[str, Any]:
        if nbytes < 0:
            raise ValueError("cannot reserve a negative size")
        document = self._load(scope)
        row = document["owners"].setdefault(str(owner_id), {"modules": {}, "persistent": 0, "temp": 0})
        used = sum(int(value) for value in row["modules"].values())
        limit = int(limit_bytes if limit_bytes is not None else document["limits"].get(str(owner_id), self.default_limit))
        if used + nbytes > limit:
            raise QuotaExceeded(
                f"{human_bytes(used + nbytes)} exceeds the {human_bytes(limit)} quota "
                f"({human_bytes(max(0, limit - used))} left)"
            )
        row["modules"][module] = int(row["modules"].get(module, 0)) + nbytes
        row["temp" if temporary else "persistent"] = int(row.get("temp" if temporary else "persistent", 0)) + nbytes
        self._save(scope, document)
        return self.usage(scope, owner_id)

    def release(self, scope: str, owner_id: int, module: str, nbytes: int, *, temporary: bool = False) -> dict[str, Any]:
        document = self._load(scope)
        row = document["owners"].setdefault(str(owner_id), {"modules": {}, "persistent": 0, "temp": 0})
        row["modules"][module] = max(0, int(row["modules"].get(module, 0)) - nbytes)
        key = "temp" if temporary else "persistent"
        row[key] = max(0, int(row.get(key, 0)) - nbytes)
        if not row["modules"][module]:
            row["modules"].pop(module, None)
        self._save(scope, document)
        return self.usage(scope, owner_id)

    def overview(self, scope: str) -> dict[str, Any]:
        document = self._load(scope)
        totals: dict[str, int] = {}
        owners = []
        grand = 0
        for owner, row in document["owners"].items():
            used = sum(int(value) for value in (row.get("modules") or {}).values())
            grand += used
            for module, value in (row.get("modules") or {}).items():
                totals[module] = totals.get(module, 0) + int(value)
            owners.append({"owner_id": int(owner), "bytes": used, "human": human_bytes(used)})
        owners.sort(key=lambda row: row["bytes"], reverse=True)
        return {
            "total": grand,
            "total_human": human_bytes(grand),
            "modules": {key: {"bytes": value, "human": human_bytes(value)} for key, value in totals.items()},
            "owners": owners[:50],
            "owner_count": len(owners),
        }


__all__ = ["JobLedger", "LedgerEntry", "QuotaExceeded", "StorageLedger", "human_bytes"]
