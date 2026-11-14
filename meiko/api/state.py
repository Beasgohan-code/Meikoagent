"""Small shared API state (maintenance flag) kept explicit instead of globals."""

from __future__ import annotations

_state = {"maintenance": False}


def set_maintenance(enabled: bool) -> None:
    _state["maintenance"] = bool(enabled)


def is_maintenance() -> bool:
    return _state["maintenance"]
