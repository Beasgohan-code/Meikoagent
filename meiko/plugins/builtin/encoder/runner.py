"""FFmpeg/ffprobe process handling.

Kept apart from the planner so the command building is testable and the process
handling is the only thing that needs a real binary.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from meiko.modules import toolchain

from .plans import EncodeSettings, build_command, duration_of, parse_progress


class FFmpegMissing(RuntimeError):
    """Raised when ``ffmpeg``/``ffprobe`` is not installed — never silently skipped."""


def ffmpeg_path() -> str:
    """Resolved through the shared toolchain (PATH → env override → wheel)."""
    return toolchain.path_of("ffmpeg")


def ffprobe_path() -> str:
    return toolchain.path_of("ffprobe")


def tools_available() -> bool:
    """Both binaries present.

    ``ffprobe`` is part of the same upstream package as ``ffmpeg``; the bundled
    wheel ships only ``ffmpeg``, so this stays strict and
    :func:`probe` transparently falls back to parsing the ffmpeg banner.
    """
    return toolchain.complete()


def missing_tools() -> list[str]:
    return toolchain.missing()


async def probe(path: str | Path, *, timeout: float = 30.0) -> dict[str, Any]:
    """``ffprobe -show_format -show_streams`` as JSON.

    When ``ffprobe`` is absent but ``ffmpeg`` is present, the banner is parsed
    instead (same dict shape) — see :mod:`meiko.modules.toolchain`.
    """
    return await toolchain.probe(path, timeout=timeout)


@dataclass
class EncodeResult:
    ok: bool
    output: str = ""
    log_tail: str = ""
    returncode: int = 0


async def encode(
    source: str | Path,
    output: str | Path,
    settings: EncodeSettings,
    *,
    duration: int = 0,
    has_subtitles: bool = False,
    has_audio: bool = True,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    progress_interval: float = 3.0,
    limit_seconds: float = 6 * 3600,
) -> EncodeResult:
    """Run one encode, reporting progress through ``on_progress``.

    The progress callback is throttled: FFmpeg emits ``-progress`` blocks many
    times a second and Telegram edits are rate limited, so the caller gets at
    most one update per ``progress_interval``.
    """
    binary = ffmpeg_path()
    if not binary:
        raise FFmpegMissing("ffmpeg is not installed")

    args = build_command(
        str(source),
        str(output),
        settings,
        duration=duration,
        has_subtitles=has_subtitles,
        has_audio=has_audio,
    )
    args[0] = binary
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    tail: list[str] = []
    last_report = 0.0
    loop = asyncio.get_event_loop()
    assert process.stdout is not None
    started = loop.time()
    try:
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace")
            tail.append(text)
            del tail[:-40]
            if on_progress is not None and text.startswith(("out_time", "frame=", "speed=", "fps=")):
                now = loop.time()
                if now - last_report >= progress_interval:
                    last_report = now
                    stats = parse_progress(text, duration)
                    if stats["percent"] or stats["seconds"]:
                        await on_progress(stats)
            if loop.time() - started > limit_seconds:
                process.kill()
                return EncodeResult(False, str(output), "".join(tail), -9)
        returncode = await process.wait()
    finally:
        if process.returncode is None:  # pragma: no cover - cancelled early
            process.kill()
            await process.wait()
    return EncodeResult(returncode == 0, str(output), "".join(tail), returncode)


def probe_duration_sync(probe_data: dict[str, Any]) -> int:
    return duration_of(probe_data)


__all__ = [
    "EncodeResult",
    "FFmpegMissing",
    "encode",
    "ffmpeg_path",
    "ffprobe_path",
    "missing_tools",
    "probe",
    "probe_duration_sync",
    "tools_available",
]
