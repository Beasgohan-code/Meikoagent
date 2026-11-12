"""reports plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="reports",
    name="Reports & Appeals",
    version="1.0.0",
    description="Members report messages; moderators resolve from an inline queue and appeals.",
    category="moderation",
    icon="flag",
    permissions=["messages.send", "moderation.read", "storage.read", "storage.write"],
    commands=["report", "reports", "appeal"],
    events=["message", "callback"],
    config_schema={
        "auto_forward_to_admins": {"type": "bool", "default": True, "label": "Notify admins"},
        "rate_limit_minutes": {"type": "int", "default": 5, "label": "Report cooldown (minutes)"},
    },
)
