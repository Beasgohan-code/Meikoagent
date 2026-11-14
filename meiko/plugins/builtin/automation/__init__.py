"""``automation`` plugin package.

Layout:
  manifest.py  typed manifest (identity, permissions, config schema)
  plugin.py    lifecycle hooks and handlers
"""

from __future__ import annotations

from .manifest import MANIFEST
from .plugin import AutomationPlugin, conditions_match, validate_workflow

__all__ = ["MANIFEST", "AutomationPlugin", "conditions_match", "validate_workflow"]
