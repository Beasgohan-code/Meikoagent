"""Heartbeat bookkeeping: uptime, latency stats and restart detection.

The plugin pings Telegram on a timer so free-tier hosts do not idle the process
out, but a ping is only useful if failures are *visible*.  This module keeps the
small state machine: consecutive failures → degraded, threshold exceeded →
alert the owner once, recovery → "back online" once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

STATUS_LABELS = {
    "healthy": "healthy",
    "degraded": "degraded",
    "down": "down",
    "unknown": "not started",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime | None = None) -> str:
    return (moment or utcnow()).isoformat(timespec="seconds")


@dataclass
class PingSample:
    at: str
    latency_ms: int
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at, "latency_ms": self.latency_ms, "ok": self.ok, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PingSample":
        return cls(
            at=str(data.get("at", "")),
            latency_ms=int(data.get("latency_ms", 0) or 0),
            ok=bool(data.get("ok", False)),
            detail=str(data.get("detail", "")),
        )


@dataclass
class HeartbeatState:
    started_at: str = ""
    last_ping_at: str = ""
    last_ok_at: str = ""
    consecutive_failures: int = 0
    total_pings: int = 0
    total_failures: int = 0
    alert_sent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "last_ping_at": self.last_ping_at,
            "last_ok_at": self.last_ok_at,
            "consecutive_failures": self.consecutive_failures,
            "total_pings": self.total_pings,
            "total_failures": self.total_failures,
            "alert_sent": self.alert_sent,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "HeartbeatState":
        if not isinstance(data, dict):
            return cls()
        state = cls()
        for name in (
            "started_at",
            "last_ping_at",
            "last_ok_at",
        ):
            setattr(state, name, str(data.get(name, "")))
        for name in ("consecutive_failures", "total_pings", "total_failures"):
            setattr(state, name, int(data.get(name, 0) or 0))
        state.alert_sent = bool(data.get("alert_sent", False))
        return state

    def status(self, failure_threshold: int = 3) -> str:
        if not self.total_pings:
            return "unknown"
        if self.consecutive_failures >= failure_threshold:
            return "down"
        if self.consecutive_failures:
            return "degraded"
        return "healthy"

    @property
    def uptime(self) -> timedelta:
        try:
            started = datetime.fromisoformat(self.started_at)
        except ValueError:
            return timedelta(0)
        return max(timedelta(0), utcnow() - started)


class HeartbeatTracker:
    """Records samples and decides when the owner must be told something."""

    def __init__(
        self,
        state: HeartbeatState | None = None,
        samples: list[dict[str, Any]] | None = None,
        history: int = 50,
    ) -> None:
        self.state = state or HeartbeatState(started_at=iso())
        self.history = history
        self._samples: list[PingSample] = [
            PingSample.from_dict(raw) for raw in (samples or [])
        ][:history]

    # -- recording -------------------------------------------------------
    def record(
        self,
        latency_ms: int,
        ok: bool,
        detail: str = "",
        *,
        failure_threshold: int = 3,
    ) -> str | None:
        """Returns an alert key (``down`` / ``recovered`` / None)."""
        sample = PingSample(at=iso(), latency_ms=int(latency_ms), ok=ok, detail=detail[:120])
        self._samples.insert(0, sample)
        del self._samples[self.history :]
        self.state.total_pings += 1
        self.state.last_ping_at = sample.at
        alert: str | None = None
        if ok:
            self.state.last_ok_at = sample.at
            if self.state.alert_sent:
                alert = "recovered"
                self.state.alert_sent = False
            self.state.consecutive_failures = 0
        else:
            self.state.total_failures += 1
            self.state.consecutive_failures += 1
            if (
                self.state.consecutive_failures >= failure_threshold
                and not self.state.alert_sent
            ):
                alert = "down"
                self.state.alert_sent = True
        return alert

    # -- reporting -------------------------------------------------------
    @property
    def samples(self) -> list[PingSample]:
        return list(self._samples)

    def latency_summary(self) -> dict[str, int]:
        values = [sample.latency_ms for sample in self._samples if sample.ok]
        if not values:
            return {"min": 0, "avg": 0, "max": 0, "count": 0}
        return {
            "min": min(values),
            "avg": int(mean(values)),
            "max": max(values),
            "count": len(values),
        }

    def success_rate(self) -> float:
        if not self.state.total_pings:
            return 0.0
        return round(100 * (1 - self.state.total_failures / self.state.total_pings), 1)

    def summary_rows(self, failure_threshold: int = 3) -> list[tuple[str, str]]:
        status = STATUS_LABELS.get(self.state.status(failure_threshold), "unknown")
        latency = self.latency_summary()
        return [
            ("Status", status),
            ("Uptime", format_duration(self.state.uptime.total_seconds())),
            ("Pings", str(self.state.total_pings)),
            ("Success", f"{self.success_rate()}%"),
            ("Latency", f"{latency['avg']} ms avg / {latency['max']} ms max" if latency["count"] else "n/a"),
            ("Last OK", self.state.last_ok_at or "never"),
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.to_dict(),
            "samples": [sample.to_dict() for sample in self._samples],
        }

    @classmethod
    def from_dict(cls, data: Any, history: int = 50) -> "HeartbeatTracker":
        if not isinstance(data, dict):
            return cls(history=history)
        return cls(
            state=HeartbeatState.from_dict(data.get("state")),
            samples=list(data.get("samples", []) or []),
            history=history,
        )


def format_duration(seconds: float) -> str:
    total = int(max(0, seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def next_ping_due(last_ping_at: str, interval_seconds: int, now: datetime | None = None) -> bool:
    if not last_ping_at:
        return True
    try:
        last = datetime.fromisoformat(last_ping_at)
    except ValueError:
        return True
    return (now or utcnow()) - last >= timedelta(seconds=max(5, interval_seconds))


__all__ = [
    "STATUS_LABELS",
    "HeartbeatState",
    "HeartbeatTracker",
    "PingSample",
    "format_duration",
    "iso",
    "next_ping_due",
    "utcnow",
]
