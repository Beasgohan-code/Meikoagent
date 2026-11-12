"""``music`` manifest – index, search, tag and cover-art toolkit."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="music",
    name="Music Toolkit",
    version="1.0.0",
    description=(
        "Index audio files, fuzzy-search the local catalogue, write ID3 tags "
        "(title/artist/album/year/track) and embed cover art – no external services."
    ),
    category="media",
    icon="music",
    permissions=["messages.send", "messages.read", "storage.read", "storage.write"],
    commands=[
        "music",
        "musicsearch",
        "addmusic",
        "tag",
        "magic",
        "cover",
        "musicstats",
        "musictags",
        "delmusic",
    ],
    events=["message"],
    resources={"cpu": "low"},
    config_schema={
        "index_limit": {"type": "int", "default": 3000, "label": "Maximum indexed tracks"},
        "results": {"type": "int", "default": 8, "label": "Search results per page"},
        "auto_tag": {"type": "bool", "default": True, "label": "Tag uploaded audio automatically"},
        "write_tags": {"type": "bool", "default": True, "label": "Allow writing ID3 tags"},
        "embed_cover": {"type": "bool", "default": True, "label": "Add generated cover art"},
        "send_as_document": {"type": "bool", "default": True, "label": "Re-send tagged files as documents"},
        "max_file_mb": {"type": "int", "default": 20, "label": "Tagging size limit (MB)"},
        "fetch_remote": {"type": "bool", "default": False, "label": "Allow remote metadata lookup"},
    },
)
