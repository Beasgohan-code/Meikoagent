"""What this build can and cannot do, in one place.

The dashboards show these facts verbatim, so the operator never has to guess
whether a missing feature is a bug or an environment gap.
"""

from __future__ import annotations

import shutil
from typing import Any

from meiko.modules.mtproto import capabilities as mtproto_capabilities


def describe() -> dict[str, Any]:
    mtproto = mtproto_capabilities()
    return {
        "transports": {
            "web_socket": True,  # /api/v1/music/live
            "sse": True,  # /api/v1/music/stream (proxy-friendly fallback)
            "polling": True,
        },
        "media": {
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "ffprobe": bool(shutil.which("ffprobe")),
        },
        "transfer": {
            "local": True,
            "http": True,
            "telegram": True,  # requires a running bot to upload
            "cloud": bool(shutil.which("rclone")),
        },
        "mtproto": mtproto.to_dict(),
        "player": {
            "backend": "voice chat",  # pytgcalls/wzgram are optional
            "simulation_when_missing": True,
        },
    }


__all__ = ["describe"]
