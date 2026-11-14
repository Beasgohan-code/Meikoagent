"""``ai`` plugin package.

Layout:
  manifest.py  typed manifest (identity, permissions, config schema)
  plugin.py    lifecycle hooks and handlers
"""

from __future__ import annotations

from .manifest import MANIFEST
from .plugin import AIPlugin

__all__ = ["MANIFEST", "AIPlugin"]
