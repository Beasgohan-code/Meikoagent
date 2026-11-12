"""welcome plugin manifest — the typed contract of the package."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="welcome",
    name="Welcome & Goodbye",
    version="1.0.0",
    description="Greets new members with premium-emoji cards, buttons and optional verification.",
    category="management",
    icon="spark",
    permissions=["messages.send", "groups.read", "messages.delete"],
    commands=["setwelcome", "welcome", "goodbye"],
    events=["join", "leave"],
    config_schema={
        "welcome_text": {
            "type": "string",
            "default": "Welcome {user} to {chat}!",
            "label": "Welcome template",
        },
        "goodbye_enabled": {"type": "bool", "default": False, "label": "Say goodbye"},
        "goodbye_text": {"type": "string", "default": "{user} left the chat.", "label": "Goodbye template"},
        "auto_delete_seconds": {"type": "int", "default": 0, "label": "Delete greeting after (0 = keep)"},
        "button_text": {"type": "string", "default": "", "label": "Optional custom button label"},
        "button_url": {"type": "string", "default": "", "label": "Optional custom button URL"},
    },
)
