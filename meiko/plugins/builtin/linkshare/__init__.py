"""``linkshare`` plugin package — protected channel links with access gating."""

from .gate import (
    Facts,
    LinkRegistry,
    ProtectedLink,
    RequiredChat,
    Verdict,
    evaluate,
    token_for,
    verdict_copy,
)
from .manifest import MANIFEST
from .plugin import LinkSharePlugin  # noqa: F401 - re-exported for the manager

__all__ = [
    "MANIFEST",
    "MANIFEST",
    "Facts",
    "LinkRegistry",
    "ProtectedLink",
    "RequiredChat",
    "Verdict",
    "evaluate",
    "token_for",
    "verdict_copy",
]
