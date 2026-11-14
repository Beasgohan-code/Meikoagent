"""``post_maker`` plugin package.

Layout:
  templates.py  HTML templates, buttons, validation and rendering (pure)
  scheduler.py  scheduled-post queue with an injectable clock (pure)
  plugin.py     commands and the scheduler job
"""

from __future__ import annotations

from .manifest import MANIFEST
from .plugin import PostMakerPlugin
from .scheduler import PostQueue, ScheduledPost, format_when, parse_when
from .templates import (
    ButtonSpec,
    PostTemplate,
    parse_buttons,
    preview_text,
    render_post,
    suggest_fill,
    validate_html,
)

__all__ = [
    "MANIFEST",
    "PostMakerPlugin",
    "ButtonSpec",
    "PostTemplate",
    "PostQueue",
    "ScheduledPost",
    "format_when",
    "parse_buttons",
    "parse_when",
    "preview_text",
    "render_post",
    "suggest_fill",
    "validate_html",
]
