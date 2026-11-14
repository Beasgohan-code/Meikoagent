"""``saver`` plugin package.

Layout:
  links.py     t.me link/range/list parsing (pure)
  captions.py  caption templates + word rules (pure)
  state.py     config dataclass, saved-post index, validation (pure)
  plugin.py    commands and the copy/re-upload pipeline
"""

from __future__ import annotations

from .captions import (
    DEFAULT_CAPTION,
    CaptionContext,
    WordRules,
    duration,
    human_size,
    render_caption,
)
from .links import LinkPlan, LinkTarget, MessageRange, parse_link, plan_links
from .manifest import MANIFEST
from .plugin import SaverPlugin
from .state import SavedIndex, SavedPost, SaverConfig, validate_caption

__all__ = [
    "MANIFEST",
    "SaverPlugin",
    "DEFAULT_CAPTION",
    "CaptionContext",
    "WordRules",
    "duration",
    "human_size",
    "render_caption",
    "LinkPlan",
    "LinkTarget",
    "MessageRange",
    "parse_link",
    "plan_links",
    "SavedIndex",
    "SavedPost",
    "SaverConfig",
    "validate_caption",
]
