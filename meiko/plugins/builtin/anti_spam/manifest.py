"""anti_spam plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="anti_spam",
    name="Anti-Spam Shield",
    version="1.0.0",
    description="Flood, duplicate, link, invite, mention and keyword protection with escalation.",
    category="security",
    icon="shield",
    permissions=["moderation.read", "moderation.write", "messages.send", "messages.delete"],
    commands=["antispam", "spamcheck"],
    events=["message"],
    config_schema={
        "flood_messages": {"type": "int", "default": 7, "label": "Messages per window"},
        "flood_window_seconds": {"type": "int", "default": 8, "label": "Window (seconds)"},
        "action": {
            "type": "choice",
            "choices": ["warn", "mute", "kick", "ban"],
            "default": "mute",
            "label": "Default punishment",
        },
        "block_links": {"type": "bool", "default": True, "label": "Delete links"},
        "block_invites": {"type": "bool", "default": True, "label": "Delete invite links"},
        "blocked_keywords": {"type": "list", "default": [], "label": "Blocked keywords"},
    },
    resources={"cpu": "low"},
)
