"""``encoder`` manifest – video re-encoding, stream swapping, thumbnails."""

from __future__ import annotations

from meiko.plugins.manifest import PluginManifest

MANIFEST = PluginManifest(
    slug="encoder",
    name="Video Encoder",
    version="1.0.0",
    description=(
        "FFmpeg re-encoder: x264/x265, CRF/preset/tune, resolution and fps targets, "
        "subtitle copy/burn/extract, multi-track audio selection, watermarks, "
        "thumbnail and media-info. Plans every command as pure argv so the profile "
        "is inspectable, and says plainly when ffmpeg is not installed."
    ),
    category="media",
    icon="video",
    permissions=["messages.send", "messages.read", "storage.read", "storage.write"],
    commands=[
        "encode", "fastencode", "encodehelp", "encpreset", "enccrf", "enccodec",
        "encaudio", "encsubs", "encresolution", "encwatermark", "encthumb",
        "encinfo", "enccancel", "encstats",
    ],
    events=["message", "callback_query"],
    resources={"cpu": "high", "disk_mb": 2048},
    config_schema={
        "default_codec": {"type": "choice", "choices": ["x264", "x265", "immediate"], "default": "x265", "label": "Default video codec"},
        "default_crf": {"type": "int", "default": 26, "label": "Default CRF (0-51)"},
        "default_preset": {
            "type": "choice",
            "choices": ["ultrafast", "superfast", "veryfast", "fast", "medium", "slow"],
            "default": "superfast",
            "label": "Default x264/x265 preset",
        },
        "default_container": {"type": "choice", "choices": ["mkv", "mp4"], "default": "mkv", "label": "Default container"},
        "max_file_mb": {"type": "int", "default": 1900, "label": "Largest input accepted (MB)"},
        "max_minutes": {"type": "int", "default": 180, "label": "Longest job accepted (minutes)"},
        "auto_encode": {"type": "bool", "default": False, "label": "Encode every video sent to the chat"},
        "keep_original": {"type": "bool", "default": True, "label": "Re-upload the original after encoding"},
        "watermark_position": {
            "type": "choice",
            "choices": ["top-left", "top-right", "bottom-left", "bottom-right", "center"],
            "default": "top-left",
            "label": "Default watermark position",
        },
        "progress_interval": {"type": "float", "default": 4.0, "label": "Seconds between progress edits"},
    },
)
