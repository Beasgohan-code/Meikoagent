"""``music`` manifest – Playeon/Videl style voice-chat player **and** toolkit.

Playback commands are modelled on ``playeon/playeon`` (``playeon-bot/src/commands``)
and ``Beasgohan-code/Videl0`` (``videl/plugins``); catalogue, ID3 and cover tools
stay from the original Meiko toolkit.
"""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

PLAYBACK = [
    "play", "vplay", "stream", "fplay", "pause", "resume", "skip", "prev",
    "seek", "fw", "bw", "end", "stop", "loop", "loopq", "autoplay",
]
QUEUE = [
    "queue", "q", "clear", "remove", "move", "shuffle", "pick", "playlist",
    "savequeue", "loadqueue", "delqueue", "chart", "now", "next",
]
STATE = [
    "playing", "nowplaying", "np", "style", "mode", "speed", "volume",
    "join", "leave", "room", "vc", "vcinfo", "auth", "mode3d",
]
TOOLKIT = [
    "music", "musicsearch", "addmusic", "tag", "magic", "cover", "musicstats",
    "musictags", "delmusic",
]

MANIFEST = PluginManifest(
    slug="music",
    name="Music · VC Player",
    version="2.0.0",
    description=(
        "Playeon/Videl-style voice-chat player with a full playback surface "
        "(queue, loop, autoplay, seek, quality flags, styles, playlists, charts, "
        "premium-emoji cards) plus the local catalogue: ID3 tagging, cover art and "
        "fuzzy search. Reports honestly when the host cannot stream into voice chat."
    ),
    category="media",
    icon="vc_play",
    permissions=[
        "messages.send",
        "messages.read",
        "storage.read",
        "storage.write",
        "http.outbound",
    ],
    commands=PLAYBACK + QUEUE + STATE + TOOLKIT,
    events=["message", "callback_query"],
    resources={"cpu": "low", "memory": "low", "network": "medium"},
    config_schema={
        # --- playback ---------------------------------------------------
        "resolve_endpoint": {
            "type": "string",
            "default": "",
            "label": "Extractor endpoint (yt-dlp/relay service) for remote search",
        },
        "resolve_token": {"type": "string", "default": "", "label": "Extractor bearer token"},
        "default_quality": {
            "type": "choice",
            "choices": ["480", "720", "1080", "1440", "2160", "best"],
            "default": "1080",
            "label": "Default /vplay quality",
        },
        "max_results": {"type": "int", "default": 8, "label": "Search results per request"},
        "autoplay_default": {"type": "bool", "default": False, "label": "Autoplay by default"},
        "default_volume": {"type": "int", "default": 100, "label": "Initial volume (0-200)"},
        "allow_duplicates": {"type": "bool", "default": False, "label": "Allow the same track twice"},
        "max_duration": {"type": "int", "default": 10800, "label": "Longest track accepted (seconds)"},
        "premium_emoji": {
            "type": "bool",
            "default": True,
            "label": "Use premium custom emoji (needs an owner account with Premium)",
        },
        # --- catalogue --------------------------------------------------
        "index_limit": {"type": "int", "default": 3000, "label": "Maximum indexed tracks"},
        "results": {"type": "int", "default": 8, "label": "Catalogue results per page"},
        "auto_tag": {"type": "bool", "default": True, "label": "Tag uploaded audio automatically"},
        "write_tags": {"type": "bool", "default": True, "label": "Allow writing ID3 tags"},
        "embed_cover": {"type": "bool", "default": True, "label": "Add generated cover art"},
        "send_as_document": {"type": "bool", "default": True, "label": "Re-send tagged files as documents"},
        "max_file_mb": {"type": "int", "default": 20, "label": "Tagging size limit (MB)"},
        "fetch_remote": {"type": "bool", "default": False, "label": "Allow remote metadata lookup"},
    },
)
