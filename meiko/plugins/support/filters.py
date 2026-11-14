"""aiogram filters shared by the plugin packages.

Why a helper: a plugin router that registers ``F.text`` (or ``F.text.startswith("/")``)
without narrowing the command name swallows the commands of every plugin
registered later in the dispatcher – ``/testrename`` silently stopped working
once the saver was installed.  Every package therefore narrows its own handlers
with :class:`OwnCommand`.

aiogram also has a sharp edge here: a plain positional filter receives the
*event object* (``Message``), while a filter following a magic filter may
receive the resolved value.  ``OwnCommand`` accepts both, so the behaviour does
not depend on registration order or on how the filter is combined.
"""

from __future__ import annotations

from typing import Any, Iterable


def command_name(value: Any) -> str:
    """``"/Save@MyBot arg"`` / ``"/save"`` / ``Message`` → ``"save"``."""
    if value is None:
        return ""
    text = getattr(value, "text", value)
    if not isinstance(text, str):
        return ""
    head = text.strip().split()[0] if text.strip() else ""
    return head.split("@")[0].lstrip("/").lower()


class OwnCommand:
    """True only for a command this plugin declares."""

    __slots__ = ("commands",)

    def __init__(self, commands: Iterable[str]) -> None:
        self.commands = {str(name).lstrip("/").lower() for name in commands}

    def __call__(self, value: Any) -> bool:
        name = command_name(value)
        return bool(name) and name in self.commands

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OwnCommand {sorted(self.commands)}>"


__all__ = ["OwnCommand", "command_name"]
