"""``linkshare`` manifest – protected channel links with verified access."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="linkshare",
    name="Protected Links",
    version="1.0.0",
    description=(
        "Share links from private channels: every viewer must be a member of the "
        "required chats (join or approved request), links can expire or cap claims, "
        "admins can approve viewers, and restricted users are refused with a reason "
        "instead of a dead end."
    ),
    category="management",
    icon="link",
    permissions=["messages.send", "messages.read", "groups.read", "storage.read", "storage.write"],
    commands=[
        "protect", "unprotect", "getlink", "linkstats", "links", "require",
        "unrequire", "approve", "decline", "pendinglinks", "linkmode", "verifylink",
    ],
    events=["message", "callback_query", "chat_member"],
    resources={"cpu": "low"},
    config_schema={
        "auto_protect": {"type": "bool", "default": False, "label": "Protect every link posted by admins"},
        "default_ttl_hours": {"type": "int", "default": 24, "label": "Default link lifetime (hours, 0 = forever)"},
        "max_claims": {"type": "int", "default": 0, "label": "Default claim cap (0 = unlimited)"},
        "require_join": {"type": "bool", "default": True, "label": "Require membership before release"},
        "request_mode": {"type": "bool", "default": False, "label": "Use join requests + admin approval"},
        "verify_ttl_seconds": {"type": "int", "default": 300, "label": "How long a verify button stays valid"},
        "linkmode": {
            "type": "choice",
            "choices": ["join", "request", "open"],
            "default": "join",
            "label": "Default gate policy",
        },
        "link_secret": {"type": "string", "default": "", "label": "HMAC secret (empty = derived from scope)"},
        "notify_admins": {"type": "bool", "default": True, "label": "Ping admins on new join requests"},
    },
)
