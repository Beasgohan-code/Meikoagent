"""``vc_player`` manifest – the voice-chat player (Playeon-style)."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

COMMANDS = [
    # playback
    "play", "vplay", "stream", "fplay", "pause", "resume", "skip", "prev",
    "seek", "fw", "bw", "end", "stop", "loop", "loopq", "autoplay",
    # queue
    "queue", "q", "clear", "remove", "move", "shuffle", "pick",
    # state
    "playing", "nowplaying", "np", "style", "mode", "speed", "volume",
    # room / voice chat
    "join", "leave", "room", "vc", "vcinfo",
]

MANIFEST = PluginManifest(
    slug="vc_player",
    name="VC Player",
    version="1.0.0",
    description=(
        "Voice-chat music/video player: /play and /vplay search or take links, queue with "
        "loop/autoplay/shuffle, seek/volume/speed control, a live play card with premium "
        "emoji buttons, and an honest report when the host cannot stream into voice chat."
    ),
    category="media",
    icon="vc_play",
    permissions=["messages.send", "storage.read", "storage.write", "http.outbound"],
    commands=COMMANDS,
    events=["message", "callback_query"],
    resources={"cpu": "low", "memory": "low"},
    config_schema={
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
        "require_vc": {
            "type": "bool",
            "default": True,
            "label": "Refuse to queue when no voice-chat backend is installed",
        },
        "allow_duplicates": {"type": "bool", "default": False, "label": "Allow the same track twice"},
        "max_duration": {"type": "int", "default": 10800, "label": "Longest track accepted (seconds)"},
    },
)
