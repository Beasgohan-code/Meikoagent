"""``code_runner`` plugin package.

Layout:
  manifest.py  typed manifest (identity, permissions, config schema)
  runtime.py   isolated child-process executor (no bot/DB dependencies)
  plugin.py    gated Telegram handlers
"""

from __future__ import annotations

from .manifest import MANIFEST
from .plugin import CodeRunnerPlugin, render_result
from .runtime import RunLimits, RunResult, available_languages, run_snippet

__all__ = [
    "MANIFEST",
    "CodeRunnerPlugin",
    "render_result",
    "RunLimits",
    "RunResult",
    "available_languages",
    "run_snippet",
]
