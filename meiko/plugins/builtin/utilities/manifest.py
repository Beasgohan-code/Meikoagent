"""utilities plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="utilities",
    name="Utilities",
    version="1.0.0",
    description="Ping, id, user info, notes, time and quick polls for groups.",
    category="utilities",
    icon="hammer",
    permissions=["messages.send", "groups.read", "storage.read", "storage.write"],
    commands=["ping", "id", "info", "note", "notes", "time"],
    events=["message"],
    config_schema={
        "allow_notes": {"type": "bool", "default": True, "label": "Enable per-group notes"},
        "max_notes": {"type": "int", "default": 50, "label": "Notes per group"},
    },
)
