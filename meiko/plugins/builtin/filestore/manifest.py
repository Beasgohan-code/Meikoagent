"""``filestore`` manifest – multi-user file vault with share links."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="filestore",
    name="Multi-user FileStore",
    version="1.0.0",
    description=(
        "Multi-user file vault: every member gets a quota, collections and TTL, "
        "files can be private, public or exposed through signed single-use share "
        "links (/start s_…), plus search, tags, re-delivery as document/video/"
        "audio and a per-user usage report."
    ),
    category="storage",
    icon="folder",
    permissions=["messages.send", "messages.read", "storage.read", "storage.write"],
    commands=[
        "put", "getf", "share", "collections", "collection", "newcollection",
        "vault", "vaultsearch", "vaultusage", "revoke", "tagf", "expire",
        "vaultstats", "myfiles",
    ],
    events=["message", "callback_query"],
    resources={"cpu": "low", "disk_mb": 2048},
    config_schema={
        "global_quota_mb": {"type": "int", "default": 2048, "label": "Shared vault quota (MB)"},
        "per_user_quota_mb": {"type": "int", "default": 256, "label": "Quota per member (MB)"},
        "max_file_mb": {"type": "int", "default": 20, "label": "Largest file accepted (MB)"},
        "max_files": {"type": "int", "default": 5000, "label": "Maximum stored files"},
        "default_ttl_days": {"type": "int", "default": 30, "label": "Default retention (days, 0 = forever)"},
        "share_ttl_days": {"type": "int", "default": 7, "label": "Share-link lifetime (days)"},
        "default_visibility": {
            "type": "choice",
            "choices": ["private", "link", "public"],
            "default": "private",
            "label": "Visibility for new files",
        },
        "allow_public": {"type": "bool", "default": True, "label": "Allow public files"},
        "page_size": {"type": "int", "default": 8, "label": "Entries per page"},
        "share_secret": {
            "type": "string",
            "default": "",
            "label": "HMAC secret for share links (empty = derived from the scope)",
        },
    },
)
