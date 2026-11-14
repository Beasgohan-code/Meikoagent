"""automation plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="automation",
    name="Automation Studio",
    version="1.0.0",
    description="Visual workflows: event triggers, conditions and actions without code.",
    category="automation",
    icon="gear",
    permissions=[
        "messages.send",
        "messages.delete",
        "groups.read",
        "storage.read",
        "storage.write",
        "scheduler",
    ],
    commands=["workflow", "workflows"],
    events=["message", "join", "callback", "scheduled"],
    config_schema={
        "max_workflows": {"type": "int", "default": 20, "label": "Workflows per group"},
        "dry_run": {"type": "bool", "default": False, "label": "Dry run (log only)"},
    },
)
