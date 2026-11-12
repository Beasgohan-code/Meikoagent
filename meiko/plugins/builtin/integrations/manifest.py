"""integrations plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="integrations",
    name="Integrations",
    version="1.0.0",
    description="Signed outbound webhooks for moderation and activity events.",
    category="integrations",
    icon="link",
    permissions=["http.outbound", "storage.read", "storage.write", "events.subscribe"],
    commands=[],
    events=["moderation", "join", "message"],
    config_schema={
        "webhook_url": {"type": "string", "default": "", "required": True, "label": "Webhook URL"},
        "secret": {"type": "secret", "default": "", "label": "HMAC signing secret"},
        "timeout_seconds": {"type": "int", "default": 10, "label": "Request timeout"},
    },
)
