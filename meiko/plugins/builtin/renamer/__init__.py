"""``renamer`` plugin package.

Layout:
  manifest.py  typed manifest
  engine.py    parsing + templating (pure, no aiogram)
  plugin.py    commands, previews and re-upload
"""

from __future__ import annotations

from .engine import (
    DEFAULT_TEMPLATE,
    ParsedName,
    RenamePlan,
    clean_text,
    describe_plan,
    guess_media_type,
    parse_name,
    plan_rename,
)
from .manifest import MANIFEST
from .plugin import RenamerPlugin

__all__ = [
    "MANIFEST",
    "RenamerPlugin",
    "DEFAULT_TEMPLATE",
    "ParsedName",
    "RenamePlan",
    "clean_text",
    "describe_plan",
    "guess_media_type",
    "parse_name",
    "plan_rename",
]
