"""filters plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="filters",
    name="Filters & Triggers",
    version="1.0.0",
    description="Reply to exact/contains/regex triggers with rich responses and buttons.",
    category="automation",
    icon="search",
    permissions=["messages.send", "groups.read", "storage.read", "storage.write"],
    commands=["filter", "filters", "stopfilter"],
    events=["message"],
    config_schema={
        "max_triggers": {"type": "int", "default": 100, "label": "Maximum triggers per group"},
        "allow_regex": {"type": "bool", "default": False, "label": "Allow regex triggers"},
    },
)
