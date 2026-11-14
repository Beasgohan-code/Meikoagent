"""One Telegram progress renderer for every module.

Rate limits are the real constraint: a chat tolerates roughly one edit per
second before Telegram starts returning 429.  Everything funnels through
:class:`ProgressRenderer`, which

* throttles edits per message (default 4 s, configurable),
* renders the same layout everywhere (premium emoji + bar + ETA),
* degrades to plain ASCII when the bot cannot use custom emoji,
* never raises into the caller — a failed edit must not fail a job.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from meiko.modules.media import human_duration, human_size
from meiko.ui import Btn, esc, keyboard, message
from meiko.ui.emoji import get as get_emoji

BAR_WIDTH = 10
FILLED = "▰"
EMPTY = "▱"


class Editor(Protocol):
    """Anything that can edit a message (aiogram Bot, a fake in tests…)."""

    async def edit_message_text(self, **kwargs: Any) -> Any:  # pragma: no cover
        ...


def bar(percent: float, width: int = BAR_WIDTH, *, premium: bool = False) -> str:
    width = max(0, width)
    filled = int(round(min(100.0, max(0.0, percent)) / 100 * width))
    if premium and width:
        from meiko.modules.progress import _premium_bar  # local import keeps this module cheap

        return _premium_bar(filled, width)
    return FILLED * filled + EMPTY * (width - filled)


def _premium_bar(filled: int, width: int) -> str:
    def glyph(index: int) -> str:
        filled_here = index < filled
        if index == 0:
            name = "bar_fill_l" if filled_here else "bar_outline_l"
        elif index == width - 1 and width > 1:
            name = "bar_fill_r" if filled_here else "bar_outline_r"
        else:
            name = "bar_fill_c" if filled_here else "bar_outline_c"
        return get_emoji(name).html

    return "".join(glyph(index) for index in range(width))


@dataclass
class ProgressView:
    """Everything a progress message needs, from any module."""

    title: str
    icon: str = "loading"
    percent: float = 0.0
    detail: str = ""
    current: int = 0
    total: int = 0
    speed: str = ""
    eta: int = 0
    state: str = "running"  # running | done | failed | cancelled | queued
    rows: list[tuple[str, str, str]] = field(default_factory=list)  # (label, value, icon)
    footer: str = ""

    @property
    def icon_name(self) -> str:
        if self.state == "done":
            return "check"
        if self.state == "failed":
            return "cross"
        if self.state == "cancelled":
            return "ban"
        return self.icon

    def render(self, *, premium: bool = True) -> str:
        title = self.title if self.state != "done" else f"{self.title} · done"
        builder = message(title[:64], self.icon_name)
        for label, value, icon in self.rows[:6]:
            builder.key_value(label, str(value), icon)
        if self.state in {"running", "queued"} or self.percent:
            builder.blank()
            builder.line(f"{bar(self.percent, premium=premium)} <b>{self.percent:.1f}%</b>", raw=True)
        line = []
        if self.current and self.total:
            line.append(f"{human_size(self.current)} / {human_size(self.total)}")
        if self.speed:
            line.append(self.speed)
        if self.eta and self.state == "running":
            line.append(f"eta {human_duration(self.eta)}")
        if line:
            builder.line(" · ".join(esc(part) for part in line), raw=True)
        if self.detail:
            builder.line(esc(self.detail[:200]), raw=True)
        if self.footer:
            builder.line(self.footer, raw=True)
        return builder.build()


class ProgressRenderer:
    """Throttled, failure-tolerant message editor."""

    def __init__(
        self,
        bot: Editor | None = None,
        *,
        interval: float = 4.0,
        premium: bool = True,
        cancel_callback: str = "",
        clock=time.monotonic,
    ) -> None:
        self.bot = bot
        self.interval = interval
        self.premium = premium
        self.cancel_callback = cancel_callback
        self._clock = clock
        self.last_edit: dict[int, float] = {}
        self.edits = 0
        self.skipped = 0
        self.failures = 0

    def should_edit(self, message_id: int, *, force: bool = False) -> bool:
        now = self._clock()
        previous = self.last_edit.get(message_id)
        if force or previous is None or now - previous >= self.interval:
            self.last_edit[message_id] = now
            return True
        self.skipped += 1
        return False

    def markup(self) -> Any:
        if not self.cancel_callback:
            return None
        return keyboard(
            [[Btn("Cancel", callback_data=self.cancel_callback, emoji="cross", style="danger")]]
        )

    async def update(
        self,
        chat_id: int,
        message_id: int,
        view: ProgressView,
        *,
        force: bool = False,
        with_cancel: bool = True,
    ) -> bool:
        """Edit the message at most once per ``interval``. Returns True when edited."""
        if self.bot is None:
            return False
        if not force and not self.should_edit(message_id):
            return False
        try:
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=view.render(premium=self.premium),
                reply_markup=self.markup() if with_cancel else None,
                disable_web_page_preview=True,
            )
            self.edits += 1
            return True
        except Exception:
            # "message is not modified", flood limits, deleted messages: none of
            # these may break the job that is reporting progress
            self.failures += 1
            return False

    def reset(self, message_id: int | None = None) -> None:
        if message_id is None:
            self.last_edit.clear()
        else:
            self.last_edit.pop(message_id, None)


def progress_view(
    title: str,
    *,
    percent: float = 0.0,
    current: int = 0,
    total: int = 0,
    speed: str = "",
    eta: int = 0,
    detail: str = "",
    state: str = "running",
    icon: str = "loading",
) -> ProgressView:
    return ProgressView(
        title=title,
        icon=icon,
        percent=percent,
        detail=detail,
        current=current,
        total=total,
        speed=speed,
        eta=eta,
        state=state,
    )


__all__ = [
    "BAR_WIDTH",
    "Editor",
    "ProgressRenderer",
    "ProgressView",
    "bar",
    "progress_view",
]
