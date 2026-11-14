"""``renamer`` manifest – template based auto-rename."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="renamer",
    name="Renamer",
    version="1.0.0",
    description=(
        "Auto-rename incoming files with templates ({title}, {season}, {episode}, {quality}…), "
        "clean release tags, re-send as document/video/audio and preview before upload."
    ),
    category="media",
    icon="hammer",
    permissions=["messages.send", "storage.read", "storage.write"],
    commands=["autorename", "rename", "testrename", "renamemode", "clean"],
    events=["message"],
    resources={"cpu": "low"},
    config_schema={
        "template": {
            "type": "string",
            "default": "{title} S{season:02d}E{episode:02d} [{quality}]",
            "label": "Default rename template",
        },
        "mode": {
            "type": "choice",
            "choices": ["off", "auto", "manual"],
            "default": "manual",
            "label": "Behaviour for incoming files",
        },
        "send_as": {
            "type": "choice",
            "choices": ["document", "video", "audio", "auto"],
            "default": "document",
            "label": "Send renamed file as",
        },
        "clean_tags": {"type": "bool", "default": True, "label": "Strip release-group noise"},
        "replace_spaces": {"type": "bool", "default": True, "label": "Collapse spaces in names"},
        "keep_extension": {"type": "bool", "default": True, "label": "Preserve the original extension"},
        "episode_offset": {"type": "int", "default": 0, "label": "Episode offset"},
        "max_name_length": {"type": "int", "default": 120, "label": "Maximum name length"},
        "dry_run": {"type": "bool", "default": False, "label": "Never upload, only preview"},
    },
)
