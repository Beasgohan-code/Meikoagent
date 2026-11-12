"""``saver`` manifest – Telegram post archiving with captions and rules."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="saver",
    name="Post Saver",
    version="1.0.0",
    description=(
        "Copy posts from public and private channels into this bot: single links, "
        "ranges (100-120), comma lists, forum topics. Custom captions, word "
        "delete/replace rules, thumbnails, duplicate skipping and a dump chat."
    ),
    category="storage",
    icon="download",
    permissions=[
        "messages.send",
        "messages.read",
        "storage.read",
        "storage.write",
        "chat.history",
    ],
    commands=[
        "save",
        "saver",
        "savestats",
        "saved",
        "setcaption",
        "resetcaption",
        "caption",
        "delword",
        "replaceword",
        "clearwords",
        "setthumb",
        "removethumb",
        "setdump",
        "savemode",
        "savebatch",
    ],
    events=["message"],
    resources={"cpu": "low", "network": "medium"},
    config_schema={
        "caption_template": {
            "type": "string",
            "default": "{filename}\n\n<code>{size}</code>",
            "label": "Caption template",
            "hint": "Placeholders: {filename} {name} {ext} {size} {title} {chat} {date} {duration}",
        },
        "use_caption": {"type": "bool", "default": False, "label": "Replace original captions"},
        "send_mode": {
            "type": "choice",
            "choices": ["copy", "document", "video"],
            "default": "copy",
            "label": "Send saved posts as",
        },
        "max_batch": {"type": "int", "default": 20, "label": "Messages per batch"},
        "delay_seconds": {"type": "float", "default": 0.4, "label": "Delay between messages"},
        "skip_duplicates": {"type": "bool", "default": True, "label": "Skip duplicates"},
        "auto_save": {"type": "bool", "default": False, "label": "Auto-save plain t.me links"},
        "allow_usernames": {"type": "bool", "default": True, "label": "Accept @channel links"},
        "max_messages": {"type": "int", "default": 500, "label": "Messages per request"},
        "max_file_mb": {"type": "int", "default": 45, "label": "Re-upload size limit (MB)"},
        "delete_words": {"type": "list", "default": [], "label": "Words to delete from captions"},
        "replace_words": {"type": "list", "default": [], "label": "Replacements (old=>new)"},
        "dump_chat": {"type": "string", "default": "", "label": "Dump chat (empty = current)"},
        "thumbnail_file_id": {"type": "string", "default": "", "label": "Custom thumbnail"},
    },
)
