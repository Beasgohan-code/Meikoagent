"""``keep_alive`` manifest – self-ping and heartbeat watchdog."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="keep_alive",
    name="Keep Alive",
    version="1.0.0",
    description=(
        "Self-ping Telegram on a timer so free-tier hosts do not idle the bot out, "
        "track latency and failure streaks, and alert the owner once when the "
        "connection drops (and once when it recovers)."
    ),
    category="utilities",
    icon="heartbeat",
    permissions=["messages.send", "storage.read", "storage.write", "scheduler"],
    commands=["keepalive", "ping", "heartbeat", "keepaliveon", "keepaliveoff"],
    events=[],
    scheduler=[{"name": "self_ping", "interval_seconds": 300, "run_immediately": True}],
    resources={"cpu": "low", "network": "low"},
    config_schema={
        "interval_seconds": {"type": "int", "default": 300, "label": "Ping interval (seconds)"},
        "failure_threshold": {"type": "int", "default": 3, "label": "Failures before alerting"},
        "notify_owner": {"type": "bool", "default": True, "label": "Alert the owner"},
        "notify_chat": {"type": "string", "default": "", "label": "Extra chat to notify"},
        "quiet_hours": {"type": "bool", "default": False, "label": "Mute alerts 00:00-06:00 UTC"},
        "history": {"type": "int", "default": 50, "label": "Samples to keep"},
        "ping_timeout_seconds": {"type": "int", "default": 10, "label": "Ping timeout"},
        "report_to_chat": {"type": "bool", "default": False, "label": "Post a daily health line"},
    },
)
