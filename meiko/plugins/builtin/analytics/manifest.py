"""analytics plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="analytics",
    name="Analytics & Digests",
    version="1.0.0",
    description="Message counters, daily digests and top-member leaderboards per group.",
    category="analytics",
    icon="stats",
    permissions=["messages.send", "groups.read", "storage.read", "storage.write", "scheduler"],
    commands=["analytics", "top", "digest"],
    events=["message"],
    config_schema={
        "track_members": {"type": "bool", "default": True, "label": "Track member activity"},
        "digest_hour": {"type": "int", "default": 20, "label": "Daily digest hour (0-23)"},
        "top_size": {"type": "int", "default": 10, "label": "Leaderboard size"},
    },
)
