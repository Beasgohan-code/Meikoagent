"""Queue of scheduled posts.

Kept intentionally simple and pure: a list of :class:`ScheduledPost` records with
``due()``/``mark_sent()`` semantics that the plugin drives from a periodic job.
Because the clock is injected, the scheduling rules are unit-testable without
sleeping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

MAX_PENDING = 50
RELATIVE_RE = re.compile(r"^\+(?P<amount>\d{1,3})(?P<unit>[smhd])$", re.I)
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_when(text: str, reference: datetime | None = None) -> datetime | None:
    """``+10m``, ``+2h``, ``+1d`` or ``YYYY-MM-DDTHH:MM`` (UTC)."""
    value = (text or "").strip()
    if not value:
        return None
    relative = RELATIVE_RE.match(value)
    if relative:
        base = reference or now_utc()
        seconds = int(relative.group("amount")) * UNIT_SECONDS[relative.group("unit").lower()]
        return base + timedelta(seconds=seconds)
    for template in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, template).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def format_when(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@dataclass
class ScheduledPost:
    id: str
    chat_id: int | str
    text: str
    send_at: str
    buttons: list[dict[str, str]] = field(default_factory=list)
    author_id: int = 0
    sent: bool = False
    sent_at: str = ""
    error: str = ""

    @property
    def when(self) -> datetime:
        try:
            return datetime.fromisoformat(self.send_at)
        except ValueError:
            return now_utc()

    def due(self, reference: datetime | None = None) -> bool:
        return not self.sent and self.when <= (reference or now_utc())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "text": self.text,
            "send_at": self.send_at,
            "buttons": self.buttons,
            "author_id": self.author_id,
            "sent": self.sent,
            "sent_at": self.sent_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduledPost":
        return cls(
            id=str(data.get("id", "")),
            chat_id=data.get("chat_id", 0),
            text=str(data.get("text", "")),
            send_at=str(data.get("send_at", "")),
            buttons=list(data.get("buttons", []) or []),
            author_id=int(data.get("author_id", 0) or 0),
            sent=bool(data.get("sent", False)),
            sent_at=str(data.get("sent_at", "")),
            error=str(data.get("error", "")),
        )


class PostQueue:
    def __init__(self, items: list[dict[str, Any]] | None = None, limit: int = MAX_PENDING) -> None:
        self.limit = limit
        self._items: list[ScheduledPost] = [ScheduledPost.from_dict(raw) for raw in items or []]

    def __len__(self) -> int:
        return len(self._items)

    @property
    def pending(self) -> list[ScheduledPost]:
        return [item for item in self._items if not item.sent]

    def add(self, post: ScheduledPost) -> bool:
        if len(self.pending) >= self.limit:
            return False
        self._items.append(post)
        return True

    def due(self, reference: datetime | None = None) -> list[ScheduledPost]:
        return [item for item in self.pending if item.due(reference)]

    def cancel(self, post_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if not (item.id == post_id and not item.sent)]
        return len(self._items) != before

    def upcoming(self, limit: int = 5) -> list[ScheduledPost]:
        return sorted(self.pending, key=lambda item: item.when)[:limit]

    def to_list(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._items]


__all__ = [
    "MAX_PENDING",
    "PostQueue",
    "ScheduledPost",
    "format_when",
    "now_utc",
    "parse_when",
]
