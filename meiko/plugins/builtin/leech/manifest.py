"""``leech`` manifest – mirror/leech task engine (CBML parity)."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="leech",
    name="Mirror & Leech",
    version="1.0.0",
    description=(
        "Mirror/leech engine: submit local paths or http(s) URLs, watch live "
        "progress/ETA/speed, cancel, and read per-user reports. Transfers go "
        "through a pluggable backend (local copy/download works out of the box; "
        "aria2/qBittorrent/rclone hooks report plainly when no daemon answers)."
    ),
    category="storage",
    icon="download",
    permissions=["messages.send", "messages.read", "storage.read", "storage.write", "http.outbound"],
    commands=["leech", "mirror", "tasklist", "taskstatus", "cancel", "leechstats", "taskbackend"],
    events=["message", "callback_query"],
    resources={"cpu": "medium", "disk_mb": 4096, "network": "high"},
    config_schema={
        "backend": {
            "type": "choice",
            "choices": ["local", "aria2", "qbit", "rclone"],
            "default": "local",
            "label": "Transfer backend",
        },
        "daemon_endpoint": {"type": "string", "default": "", "label": "Daemon URL (aria2/qBittorrent/rclone RC)"},
        "max_active": {"type": "int", "default": 2, "label": "Concurrent transfers"},
        "max_per_user": {"type": "int", "default": 2, "label": "Concurrent transfers per user"},
        "max_download_mb": {"type": "int", "default": 4096, "label": "Largest transfer accepted (MB)"},
        "progress_interval": {"type": "float", "default": 4.0, "label": "Seconds between progress edits"},
        "keep_files": {"type": "bool", "default": True, "label": "Keep completed files in the workspace"},
        "auto_upload": {"type": "bool", "default": True, "label": "Re-upload completed leeches to the chat"},
    },
)
