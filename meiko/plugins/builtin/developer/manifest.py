"""developer plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="developer",
    name="Developer Tools",
    version="1.0.0",
    description="Runtime introspection: version, Bot API coverage, plugin health, ids.",
    category="developer",
    icon="code",
    permissions=["messages.send", "storage.read"],
    commands=["dev", "runtime", "coverage"],
    events=["message"],
    config_schema={
        "show_tracebacks": {"type": "bool", "default": False, "label": "Expose tracebacks"},
    },
)
