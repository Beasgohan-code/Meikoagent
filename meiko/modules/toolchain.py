"""Where the media binaries actually come from.

Three ways a deployment gets ``ffmpeg``/``ffprobe``, in priority order:

1. **PATH** — the normal case, and what operators expect (``apt install ffmpeg``).
2. **An explicit override** — ``MEIKO_FFMPEG_BIN`` / ``MEIKO_FFPROBE_BIN``, for
   containers that mount a binary somewhere unusual.
3. **A bundled wheel** — ``imageio-ffmpeg`` ships a static ``ffmpeg`` for Linux,
   macOS and Windows. It is *ffmpeg only*: upstream does not publish ``ffprobe``.
   We therefore never claim ``ffprobe`` exists because of the wheel.

Why this module exists at all: three places used to answer "is ffmpeg here?"
by calling ``shutil.which`` directly (the encoder runner, the music player's
voice-chat check, and telemetry). They could disagree with each other the
moment a binary lived anywhere else, and telemetry could claim a capability the
encoder then failed to use. One resolver, one answer.

The probe helper is honest about the missing half: without ``ffprobe`` we can
still inspect a file by parsing ``ffmpeg -i``'s banner, which is the same
information in a worse shape. That keeps ``/encinfo`` working on a machine with
only the bundled binary, and :func:`describe` says which path was used.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Environment overrides, checked before PATH.
FFMPEG_ENV = "MEIKO_FFMPEG_BIN"
FFPROBE_ENV = "MEIKO_FFPROBE_BIN"

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d+)")
_STREAM_RE = re.compile(r"Stream #\d+:\d+(?:\[[^\]]*\])?(?:\(([^)]*)\))?:\s*(Video|Audio|Subtitle|Data)\s*:\s*([^,\s]+)")
_VIDEO_SIZE_RE = re.compile(r",\s*(\d{2,5})x(\d{2,5})")
#: Header emitted before the input dump when probing with ``-i``.
_BANNER = "Metadata:"


@dataclass(frozen=True)
class Tool:
    """One external binary, resolved once and remembered."""

    name: str
    path: str = ""
    source: str = ""  # path | env | bundled | ""

    @property
    def available(self) -> bool:
        return bool(self.path)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "available": self.available, "path": self.path, "source": self.source}


def _bundled_ffmpeg() -> str:
    """``imageio-ffmpeg``'s static binary, if that optional wheel is installed."""
    try:  # pragma: no cover - depends on an optional dependency
        import imageio_ffmpeg  # type: ignore[import-not-found]

        return str(imageio_ffmpeg.get_ffmpeg_exe() or "")
    except Exception:
        return ""


def _bundled_ffprobe() -> str:
    """Look for an ``ffprobe`` shipped next to a bundled ``ffmpeg``.

    Some builds (and operators who drop a second binary beside the first) do
    provide one; ``imageio-ffmpeg`` upstream does not, which is exactly why we
    check instead of assuming.
    """
    ffmpeg = _bundled_ffmpeg()
    if not ffmpeg:
        return ""
    candidate = Path(ffmpeg).with_name("ffprobe" + (".exe" if ffmpeg.endswith(".exe") else ""))
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else ""


@lru_cache(maxsize=None)
def resolve(name: str) -> Tool:
    """Find ``ffmpeg`` or ``ffprobe`` once per process."""
    env_value = os.environ.get(FFMPEG_ENV if name == "ffmpeg" else FFPROBE_ENV, "").strip()
    if env_value:
        path = Path(env_value).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return Tool(name, str(path), "env")
        # An override that does not work is a configuration error worth seeing,
        # but it must not silently disable a perfectly good PATH binary.
    found = shutil.which(name)
    if found:
        return Tool(name, found, "path")
    bundled = _bundled_ffmpeg() if name == "ffmpeg" else _bundled_ffprobe()
    if bundled and os.access(bundled, os.X_OK):
        return Tool(name, bundled, "bundled")
    return Tool(name)


def path_of(name: str) -> str:
    return resolve(name).path


def has(name: str) -> bool:
    return resolve(name).available


def missing() -> list[str]:
    return [name for name in ("ffmpeg", "ffprobe") if not has(name)]


def complete() -> bool:
    return not missing()


def clear_cache() -> None:
    """Tests and long-lived processes call this after installing a tool."""
    resolve.cache_clear()


def describe() -> dict[str, Any]:
    """Capability summary used by telemetry and the encoder panel."""
    ffmpeg = resolve("ffmpeg")
    ffprobe = resolve("ffprobe")
    return {
        "ffmpeg": ffmpeg.available,
        "ffprobe": ffprobe.available,
        "sources": {"ffmpeg": ffmpeg.source, "ffprobe": ffprobe.source},
        "paths": {"ffmpeg": ffmpeg.path, "ffprobe": ffprobe.path},
        "missing": missing(),
        # True when we can run ffmpeg but must read metadata from its banner.
        "probe_fallback": ffmpeg.available and not ffprobe.available,
    }


# --------------------------------------------------------------------- probe
async def probe(path: str | Path, *, timeout: float = 30.0) -> dict[str, Any]:
    """Metadata for one file, in ``ffprobe -print_format json`` shape.

    Uses ``ffprobe`` when it exists and falls back to parsing the ``ffmpeg -i``
    banner when it does not, so callers get one stable dict either way.
    """
    target = str(path)
    if has("ffprobe"):
        payload = await _probe_json(target, timeout=timeout)
        if payload is not None:
            return payload
    return await _probe_banner(target, timeout=timeout)


async def _probe_json(target: str, *, timeout: float) -> dict[str, Any] | None:
    process = await asyncio.create_subprocess_exec(
        path_of("ffprobe"),
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        target,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:  # pragma: no cover - hung probe
        process.kill()
        raise
    if process.returncode != 0:
        return None
    try:
        return json.loads(stdout.decode(errors="replace") or "{}")
    except json.JSONDecodeError:  # pragma: no cover - ffprobe emits JSON
        return None


async def _probe_banner(target: str, *, timeout: float) -> dict[str, Any]:
    """Parse ``ffmpeg -i <file>`` output into the ffprobe dict shape.

    ffmpeg exits non-zero (``At least one output file must be specified``) after
    dumping the input — that is expected, so the return code is not checked.
    """
    if not has("ffmpeg"):
        raise FileNotFoundError("neither ffprobe nor ffmpeg is installed")
    process = await asyncio.create_subprocess_exec(
        path_of("ffmpeg"), "-hide_banner", "-i", target,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:  # pragma: no cover - hung probe
        process.kill()
        raise
    return parse_banner(stderr.decode(errors="replace"))


def parse_banner(text: str) -> dict[str, Any]:
    """Turn ffmpeg's stderr dump into ffprobe-shaped JSON (pure, testable)."""
    streams: list[dict[str, Any]] = []
    duration = 0.0
    match = _DURATION_RE.search(text)
    if match:
        hours, minutes, seconds, _frac = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    for line in text.splitlines():
        stream = _STREAM_RE.search(line)
        if not stream:
            continue
        language, kind, codec = stream.groups()
        entry: dict[str, Any] = {
            "index": len(streams),
            "codec_type": kind.lower(),
            "codec_name": codec,
        }
        if language:
            entry["tags"] = {"language": language}
        if kind == "Video":
            size = _VIDEO_SIZE_RE.search(line)
            if size:
                entry["width"], entry["height"] = int(size.group(1)), int(size.group(2))
        if kind == "Audio":
            channels = re.search(r"(\d+) channels", line)
            if channels:
                entry["channels"] = int(channels.group(1))
            rate = re.search(r"(\d+) Hz", line)
            if rate:
                entry["sample_rate"] = int(rate.group(1))
        streams.append(entry)
    return {
        "format": {"duration": str(int(duration)), "source": "ffmpeg-banner"},
        "streams": streams,
    }


def parse_metadata(text: str) -> dict[str, Any]:
    """Extras from the banner that ``/encinfo`` shows (container, bitrate, brand)."""
    info: dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Input #0"):
            info["container"] = stripped.split(",", 1)[-1].strip() if "," in stripped else stripped
        elif stripped.startswith("Duration:"):
            remainder = stripped.split(",", 1)[-1].strip()
            if "bitrate:" in remainder:
                info["bitrate"] = remainder.split("bitrate:", 1)[-1].strip()
    return info


__all__ = [
    "FFMPEG_ENV",
    "FFPROBE_ENV",
    "Tool",
    "clear_cache",
    "complete",
    "describe",
    "has",
    "missing",
    "parse_banner",
    "parse_metadata",
    "path_of",
    "probe",
    "resolve",
]
