"""Shared building blocks for the file/media plugin packages.

These helpers are deliberately *not* a plugin: they hold the pieces several
packages need (quota-aware workspace, safe filenames, owner gates) so the logic
exists once and is unit-testable on its own.
"""

from __future__ import annotations

from .files import (
    FileTooLarge,
    QuotaExceeded,
    Workspace,
    human_size,
    safe_filename,
    sanitize_ext,
)
from .gates import CapabilityGate, GateDenied
from .owners import scope_owner
from .state import PluginState, read_mirror, state_dir, write_mirror

__all__ = [
    "CapabilityGate",
    "FileTooLarge",
    "GateDenied",
    "QuotaExceeded",
    "Workspace",
    "human_size",
    "safe_filename",
    "sanitize_ext",
    "scope_owner",
    "PluginState",
    "read_mirror",
    "state_dir",
    "write_mirror",
]
