"""``keep_alive`` plugin package.

Layout:
  watchdog.py  heartbeat state machine + latency stats (pure, clock-injectable)
  plugin.py    /ping, /heartbeat, owner alerts and the self-ping job
"""

from __future__ import annotations

from .manifest import MANIFEST
from .plugin import KeepAlivePlugin
from .watchdog import (
    HeartbeatState,
    HeartbeatTracker,
    PingSample,
    format_duration,
    next_ping_due,
)

__all__ = [
    "MANIFEST",
    "KeepAlivePlugin",
    "HeartbeatState",
    "HeartbeatTracker",
    "PingSample",
    "format_duration",
    "next_ping_due",
]
