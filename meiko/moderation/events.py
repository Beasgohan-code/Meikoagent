"""Normalised moderation events.

Handlers translate aiogram updates into :class:`ModerationEvent` objects; the
rule engine, anti-spam detectors and automation share this single vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ModerationEvent:
    kind: str  # message / join / leave / reaction / callback / command / scheduled
    bot_id: int
    chat_id: int | None = None
    user_id: int | None = None
    username: str | None = None
    group_id: int | None = None
    message_id: int | None = None
    text: str = ""
    message_thread_id: int | None = None
    is_admin: bool = False
    is_trusted: bool = False
    is_bot: bool = False
    replied_user_id: int | None = None
    has_link: bool = False
    has_invite: bool = False
    has_forward: bool = False
    has_media: bool = False
    media_type: str | None = None
    mentions_count: int = 0
    emoji_count: int = 0
    character_count: int = 0
    is_new_member: bool = False
    joined_within_seconds: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    @property
    def chat_key(self) -> str:
        return f"{self.bot_id}:{self.chat_id}:{self.user_id}"

    def fact(self, name: str) -> Any:
        """Resolve a condition field, e.g. ``text.length`` or ``user.is_admin``."""
        aliases = {
            "user.id": self.user_id,
            "user.username": self.username,
            "user.is_admin": self.is_admin,
            "user.is_trusted": self.is_trusted,
            "user.is_bot": self.is_bot,
            "user.is_new_member": self.is_new_member,
            "message.text": self.text,
            "message.length": self.character_count or len(self.text or ""),
            "message.has_link": self.has_link,
            "message.has_invite": self.has_invite,
            "message.has_forward": self.has_forward,
            "message.has_media": self.has_media,
            "message.mentions": self.mentions_count,
            "message.emoji_count": self.emoji_count,
            "message.thread_id": self.message_thread_id,
            "event.kind": self.kind,
            "chat.id": self.chat_id,
        }
        if name in aliases:
            return aliases[name]
        if name in self.raw:
            return self.raw[name]
        return getattr(self, name, None)


EVENT_KINDS = {
    "message",
    "edited_message",
    "join",
    "leave",
    "reaction",
    "callback",
    "command",
    "inline_query",
    "guest_message",
    "widget_query",
    "scheduled",
}
