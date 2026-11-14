"""``file_store`` manifest – personal/group file vault."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="file_store",
    name="File Store",
    version="1.0.0",
    description=(
        "Keep files in a per-scope vault: upload with /fsave, fetch with /get, browse with /files, "
        "inspect with /storestats. Quota enforced per scope, contents never exposed to the webapp."
    ),
    category="utilities",
    icon="database",
    permissions=["messages.send", "storage.read", "storage.write"],
    commands=["fsave", "get", "files", "del", "storestats", "storequota"],
    events=["message"],
    resources={"cpu": "low", "disk_mb": 512},
    config_schema={
        "quota_mb": {"type": "int", "default": 512, "label": "Vault quota per scope (MB)"},
        "max_file_mb": {"type": "int", "default": 20, "label": "Largest file accepted (MB)"},
        "per_user_quota_mb": {"type": "int", "default": 64, "label": "Quota per member (MB)"},
        "ttl_days": {"type": "int", "default": 30, "label": "Retention before sweep (days)"},
        "allow_groups": {"type": "bool", "default": True, "label": "Allow group vaults"},
        "index_page_size": {"type": "int", "default": 10, "label": "Entries per page"},
    },
)
