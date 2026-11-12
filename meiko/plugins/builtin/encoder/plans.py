"""FFmpeg encode planning — the maths, separated from the subprocess.

Everything here is pure: given a source's probe data and a user's
:class:`EncodeSettings`, :func:`build_command` returns the exact argv FFmpeg
would receive.  That makes the interesting part (codec/bitrate/subtitle/
container decisions, modelled on ``abhinai2244/ENCODING-BOT``) testable without
a media file or an FFmpeg binary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: preset label → libx264/libx265 ``-preset`` value (X265's set is shared with x264)
PRESETS = {
    "ultrafast": "ultrafast",
    "superfast": "superfast",
    "veryfast": "veryfast",
    "faster": "faster",
    "fast": "fast",
    "medium": "medium",
    "slow": "slow",
    "slower": "slower",
    "veryslow": "veryslow",
}
CODECS = {"x264": "libx264", "x265": "libx265", "immediate": "copy"}
AUDIO_CODECS = {"aac": "aac", "opus": "libopus", "mp3": "libmp3lame", "ac3": "ac3", "flac": "flac", "copy": "copy"}
CONTAINERS = {
    "mp4": {"ext": "mp4", "subs": "mov_text", "muxer": "-movflags +faststart"},
    "mkv": {"ext": "mkv", "subs": "ass", "muxer": ""},
    "avi": {"ext": "avi", "subs": "", "muxer": ""},
}
RESOLUTIONS = {"360p": 640, "480p": 854, "576p": 1024, "720p": 1280, "1080p": 1920, "1440p": 2560, "2160p": 3840}
FRAME_RATES = {"auto": "", "23.976": "24000/1001", "24": "24", "25": "25", "29.97": "30000/1001", "30": "30", "50": "50", "60": "60"}
SUBTITLE_MODES = {"copy", "burn", "none", "extract"}
TUNES = {"", "film", "animation", "grain", "stillimage", "fastdecode", "zerolatency"}


@dataclass
class EncodeSettings:
    """Per-user encode profile (the ENCODING-BOT settings panel, made explicit)."""

    codec: str = "x265"
    crf: int = 26
    preset: str = "superfast"
    tune: str = "film"
    container: str = "mkv"
    audio_codec: str = "aac"
    audio_bitrate: str = "128k"
    audio_channels: str = "original"  # original | mono | stereo
    subtitles: str = "copy"  # copy | burn | none | extract
    resolution: str = "original"
    frame_rate: str = "auto"
    pix_fmt_10bit: bool = False
    watermark: str = ""
    watermark_position: str = "top-left"
    metadata_title: str = ""
    two_pass: bool = False
    extra_args: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "codec": self.codec,
            "crf": self.crf,
            "preset": self.preset,
            "tune": self.tune,
            "container": self.container,
            "audio_codec": self.audio_codec,
            "audio_bitrate": self.audio_bitrate,
            "audio_channels": self.audio_channels,
            "subtitles": self.subtitles,
            "resolution": self.resolution,
            "frame_rate": self.frame_rate,
            "pix_fmt_10bit": self.pix_fmt_10bit,
            "watermark": self.watermark,
            "watermark_position": self.watermark_position,
            "metadata_title": self.metadata_title,
            "two_pass": self.two_pass,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "EncodeSettings":
        row = data if isinstance(data, dict) else {}
        settings = cls()
        for key in ("codec", "preset", "tune", "container", "audio_codec", "audio_bitrate",
                    "audio_channels", "subtitles", "resolution", "frame_rate", "watermark",
                    "watermark_position", "metadata_title"):
            value = row.get(key)
            if isinstance(value, str) and value:
                setattr(settings, key, value)
        if isinstance(row.get("crf"), int | str) and str(row.get("crf")).isdigit():
            settings.crf = max(0, min(51, int(row["crf"])))
        settings.pix_fmt_10bit = bool(row.get("pix_fmt_10bit"))
        settings.two_pass = bool(row.get("two_pass"))
        return settings

    def validate(self) -> list[str]:
        """Return human-readable problems; empty means the profile is runnable."""
        problems = []
        if self.codec not in CODECS:
            problems.append(f"codec must be one of {', '.join(CODECS)}")
        if not 0 <= int(self.crf) <= 51:
            problems.append("crf must be 0–51")
        if self.preset not in PRESETS:
            problems.append(f"preset must be one of {', '.join(PRESETS)}")
        if self.container not in CONTAINERS:
            problems.append(f"container must be one of {', '.join(CONTAINERS)}")
        if self.audio_codec not in AUDIO_CODECS:
            problems.append(f"audio codec must be one of {', '.join(AUDIO_CODECS)}")
        if self.subtitles not in SUBTITLE_MODES:
            problems.append("subtitle mode must be copy, burn, none or extract")
        if self.container == "avi" and self.subtitles == "copy":
            problems.append("avi cannot carry modern subtitle tracks")
        if self.codec == "x265" and self.container == "avi":
            problems.append("avi cannot carry HEVC reliably")
        return problems


def _watermark_filter(text: str, position: str, font_size: int = 24) -> str:
    """``drawtext=...`` with the same escaping FFmpeg needs (colons and quotes)."""
    safe = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
    coords = {
        "top-left": "x=20:y=20",
        "top-right": "x=w-tw-20:y=20",
        "bottom-left": "x=20:y=h-th-20",
        "bottom-right": "x=w-tw-20:y=h-th-20",
        "center": "x=(w-tw)/2:y=(h-th)/2",
    }.get(position, "x=20:y=20")
    return (
        f"drawtext=text='{safe}':fontcolor=white@0.85:fontsize={font_size}:"
        f"box=1:boxcolor=black@0.35:boxborderw=8:{coords}"
    )


def build_command(
    source: str,
    output: str,
    settings: EncodeSettings,
    *,
    duration: int = 0,
    has_subtitles: bool = False,
    has_audio: bool = True,
    font_path: str = "",
) -> list[str]:
    """Build the FFmpeg argv for one encode. Pure and deterministic."""
    args = ["ffmpeg", "-hide_banner", "-y", "-i", source]
    filters: list[str] = []

    if settings.resolution in RESOLUTIONS and settings.resolution != "original":
        width = RESOLUTIONS[settings.resolution]
        filters.append(f"scale='min({width},iw)':-2")

    if settings.watermark:
        filters.append(_watermark_filter(settings.watermark, settings.watermark_position, 28 if settings.resolution in {"1080p", "1440p", "2160p"} else 20))

    burn_subtitles = settings.subtitles == "burn" and has_subtitles and bool(CONTAINERS[settings.container]["subs"])
    if burn_subtitles:
        filters.append("subtitles=filename='" + source.replace("'", "\\'") + "'")

    if filters:
        args += ["-vf", ",".join(filters)]

    codec = CODECS.get(settings.codec, "libx264")
    if settings.codec == "immediate":
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", codec, "-crf", str(int(settings.crf)), "-preset", PRESETS.get(settings.preset, "veryfast")]
        if settings.tune in TUNES and settings.tune:
            args += ["-tune", settings.tune]
        args += ["-pix_fmt", "yuv420p10le" if settings.pix_fmt_10bit else "yuv420p"]
        if settings.codec == "x264":
            args += ["-profile:v", "high", "-level", "4.1"]
        if settings.two_pass:
            args += ["-pass", "1"]

    if settings.frame_rate in FRAME_RATES and FRAME_RATES[settings.frame_rate]:
        args += ["-r", FRAME_RATES[settings.frame_rate]]

    args += ["-map", "0:v:0"]

    if has_audio and settings.audio_codec != "copy":
        args += ["-c:a", AUDIO_CODECS.get(settings.audio_codec, "aac"), "-b:a", settings.audio_bitrate]
        if settings.audio_channels == "mono":
            args += ["-ac", "1"]
        elif settings.audio_channels == "stereo":
            args += ["-ac", "2"]
        args += ["-map", "0:a:0?"]
    elif has_audio:
        args += ["-c:a", "copy", "-map", "0:a?"]
    else:
        args += ["-an"]

    subtitle_codec = CONTAINERS[settings.container]["subs"]
    if has_subtitles and settings.subtitles == "copy" and subtitle_codec:
        args += ["-c:s", subtitle_codec, "-map", "0:s?", "-map", "0:t?"]
    elif has_subtitles and settings.subtitles == "extract":
        args += ["-c:s", "srt", "-map", "0:s?"]
    elif settings.subtitles == "none":
        args += ["-sn"]

    if settings.metadata_title:
        args += ["-metadata", f"title={settings.metadata_title}"]
        args += ["-metadata:s:a:0", f"title={settings.metadata_title}"]

    muxer = CONTAINERS[settings.container]["muxer"]
    if muxer:
        args += muxer.split()

    args += [arg for arg in settings.extra_args if isinstance(arg, str)]
    args += ["-progress", "pipe:1", "-nostats", output]
    return args


def output_name(source_name: str, settings: EncodeSettings) -> str:
    """``Show.S01E01.mkv`` → ``Show.S01E01 [x265].mp4`` (never overwrites the source)."""
    stem = re.sub(r"\.[^.]+$", "", source_name) or "output"
    tag = settings.codec if settings.codec in {"x264", "x265"} else "copy"
    tag += f"-{settings.crf}" if settings.codec in {"x264", "x265"} else ""
    ext = CONTAINERS.get(settings.container, CONTAINERS["mkv"])["ext"]
    return f"{stem} [{tag}].{ext}"


TIME_RE = re.compile(r"out_time_ms=(\d+)")


def parse_progress(chunk: str, duration: int) -> dict[str, Any]:
    """Parse ``-progress pipe:1`` key=value blocks into a percent + eta."""
    percent = 0.0
    seconds = 0.0
    speed = ""
    fps = ""
    for line in chunk.splitlines():
        key, _, value = line.partition("=")
        key = key.strip()
        if key in ("out_time_ms", "out_time_us") and value.strip().isdigit():
            seconds = int(value) / 1_000_000
            if duration:
                percent = min(99.9, seconds / duration * 100)
        elif key == "out_time" and ":" in value:
            hours, minutes, secs = (value.strip().split(":") + ["0", "0"])[:3]
            try:
                seconds = int(hours) * 3600 + int(minutes) * 60 + float(secs)
            except ValueError:
                pass
            if duration:
                percent = min(99.9, seconds / duration * 100)
        elif key == "speed":
            speed = value.strip()
        elif key == "fps":
            fps = value.strip()
    return {"percent": round(percent, 1), "seconds": round(seconds, 1), "speed": speed, "fps": fps}


def parse_audio_streams(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """Audio tracks from ``ffprobe -show_streams`` JSON (what /encaudio lists)."""
    streams = probe.get("streams") or []
    out = []
    for stream in streams:
        if stream.get("codec_type") != "audio":
            continue
        tags = stream.get("tags") or {}
        out.append(
            {
                "index": int(stream.get("index", 0)),
                "codec": stream.get("codec_name", "?"),
                "channels": int(stream.get("channels", 0) or 0),
                "language": tags.get("language", "und"),
                "title": tags.get("title", ""),
                "bitrate": int(stream.get("bit_rate", 0) or 0),
            }
        )
    return out


def has_subtitle_stream(probe: dict[str, Any]) -> bool:
    return any(s.get("codec_type") == "subtitle" for s in probe.get("streams") or [])


def duration_of(probe: dict[str, Any]) -> int:
    try:
        return int(float((probe.get("format") or {}).get("duration", 0) or 0))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "AUDIO_CODECS",
    "CODECS",
    "CONTAINERS",
    "EncodeSettings",
    "FRAME_RATES",
    "PRESETS",
    "RESOLUTIONS",
    "SUBTITLE_MODES",
    "build_command",
    "duration_of",
    "has_subtitle_stream",
    "output_name",
    "parse_audio_streams",
    "parse_progress",
]
