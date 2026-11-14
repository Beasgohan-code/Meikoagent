"""``encoder`` plugin package — FFmpeg re-encoding with inspectable plans."""

from .manifest import MANIFEST
from .plugin import EncoderPlugin  # noqa: F401 - re-exported for the manager
from .plans import EncodeSettings, build_command, output_name, parse_progress
from .runner import FFmpegMissing, encode, missing_tools, probe, tools_available

__all__ = [
    "MANIFEST",
    "MANIFEST",
    "EncodeSettings",
    "FFmpegMissing",
    "build_command",
    "encode",
    "missing_tools",
    "output_name",
    "parse_progress",
    "probe",
    "tools_available",
]
