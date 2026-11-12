"""security plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="security",
    name="Security & Verification",
    version="1.0.0",
    description="New-member verification buttons, lockdown mode and join-raid detection.",
    category="security",
    icon="lock",
    permissions=["messages.send", "moderation.write", "messages.delete", "groups.read", "storage.write"],
    commands=["verify", "lockdown"],
    events=["join", "callback"],
    config_schema={
        "verification_enabled": {"type": "bool", "default": True, "label": "Require verification"},
        "timeout_minutes": {"type": "int", "default": 10, "label": "Verification timeout"},
        "raid_join_threshold": {"type": "int", "default": 15, "label": "Joins per minute to trigger lockdown"},
    },
)
