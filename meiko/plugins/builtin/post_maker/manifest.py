"""``post_maker`` manifest – styled posts, buttons and scheduling."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="post_maker",
    name="Post Maker",
    version="1.0.0",
    description=(
        "Build rich Telegram posts from templates: entities (bold/quote/spoiler), "
        "URL button rows, live preview, scheduling (+10m) and queue management."
    ),
    category="management",
    icon="message",
    permissions=["messages.send", "storage.read", "storage.write", "scheduler"],
    commands=[
        "post",
        "postpreview",
        "posttemplate",
        "postbuttons",
        "schedule",
        "scheduled",
        "unschedule",
    ],
    events=["message"],
    scheduler=[{"name": "post_queue", "interval_seconds": 30}],
    resources={"cpu": "low"},
    config_schema={
        "default_template": {
            "type": "string",
            "default": "<b>{title}</b>\n\n{description}\n\n<blockquote>{meta}</blockquote>",
            "label": "Default body template",
        },
        "footer": {"type": "string", "default": "", "label": "Footer appended to every post"},
        "signature": {"type": "string", "default": "", "label": "Signature (channel handle)"},
        "max_buttons": {"type": "int", "default": 4, "label": "Buttons per post"},
        "require_preview": {"type": "bool", "default": True, "label": "Preview before publishing"},
        "queue_limit": {"type": "int", "default": 50, "label": "Maximum scheduled posts"},
        "allow_scheduling": {"type": "bool", "default": True, "label": "Enable scheduling"},
    },
)
