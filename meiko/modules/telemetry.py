"""What this build can and cannot do, in one place.

The dashboards show these facts verbatim, so the operator never has to guess
whether a missing feature is a bug or an environment gap.
"""

from __future__ import annotations

import shutil
from typing import Any

from meiko.modules import leech, mtproto, toolchain

mtproto_capabilities = mtproto.capabilities


def _module_present(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - broken install
        return False


def describe() -> dict[str, Any]:
    mtproto = mtproto_capabilities()
    return {
        "transports": {
            "web_socket": True,  # /api/v1/music/live
            "sse": True,  # /api/v1/music/stream (proxy-friendly fallback)
            "polling": True,
        },
        "media": toolchain.describe(),
        "transfer": {
            "local": True,
            "http": True,
            "telegram": True,  # requires a running bot to upload
            "cloud": bool(shutil.which("rclone")),
            "magnet": leech.magnet_available(),
            "ytdlp": _module_present("yt_dlp"),
        },
        "mtproto": mtproto.to_dict(),
        "player": {
            "backend": "voice chat",  # pytgcalls/wzgram are optional
            "simulation_when_missing": True,
        },
    }


__all__ = ["describe"]
