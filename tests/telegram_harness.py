"""Shared aiogram test harness: a recording Bot session and Update builders.

Several plugin suites need the same three things — a session that answers every
Bot API call without a network, genuine ``Message``/``Update`` objects, and a
way to build replies/attachments.  Keeping them here means each suite tests
plugin behaviour instead of re-implementing transport fakes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Document, Message, PhotoSize, Update, Video
from aiogram.types import User as TgUser


class Recorder(BaseSession):
    """Answers Bot API calls and records them; returns real aiogram objects."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method, timeout: int | None = None):
        from aiogram.methods import GetMe, SendMessage

        payload = method.model_dump(exclude_none=True)
        if isinstance(method, GetMe):
            return {"id": 1, "is_bot": True, "first_name": "T", "username": "testbot"}
        self.sent.append((type(method).__name__, payload))
        if isinstance(method, SendMessage):
            chat = payload["chat_id"]
            return Message.model_validate(
                {
                    "message_id": 500 + len(self.sent),
                    "date": int(datetime.now(timezone.utc).timestamp()),
                    "chat": chat if isinstance(chat, dict) else {"id": int(chat), "type": "private"},
                    "text": payload.get("text", ""),
                }
            ).as_(bot)
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    # -- assertions helpers ------------------------------------------------
    def replies(self) -> list[str]:
        return [p.get("text", "") for name, p in self.sent if name == "SendMessage"]

    def buttons(self) -> list[str]:
        out: list[str] = []
        for _name, payload in self.sent:
            markup = payload.get("reply_markup") or {}
            for row in markup.get("inline_keyboard", []):
                out.extend(button.get("callback_data", "") for button in row)
        return [data for data in out if data]

    def answers(self) -> list[str]:
        return [p.get("text", "") for name, p in self.sent if name == "AnswerCallbackQuery"]

    def edits(self) -> list[str]:
        return [p.get("text", "") for name, p in self.sent if name == "EditMessageText"]


def make_message(bot: Bot, text: str, owner_id: int, *, message_id: int = 1, reply_to: Message | None = None) -> Message:
    user = TgUser(id=owner_id, is_bot=False, first_name="Tester").as_(bot)
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=owner_id, type="private").as_(bot),
        from_user=user,
        text=text,
        reply_to_message=reply_to,
    ).as_(bot)


def make_update(bot: Bot, text: str, owner_id: int) -> Update:
    return Update(update_id=1, message=make_message(bot, text, owner_id))


def attach(bot: Bot, owner_id: int, text: str, *, kind: str = "video", caption: str = "") -> Update:
    """A command message replying to a video/document/photo (for /encode, /put…)."""
    user = TgUser(id=owner_id, is_bot=False, first_name="Tester").as_(bot)
    parent_kwargs: dict[str, Any] = {}
    if kind == "video":
        parent_kwargs["video"] = Video(
            file_id="VID123", file_unique_id="UNIQ1", width=1920, height=1080,
            duration=60, file_name="clip.mp4", file_size=1024,
        ).as_(bot)
    elif kind == "document":
        parent_kwargs["document"] = Document(
            file_id="DOC123", file_unique_id="UNIQ2", file_name="archive.zip", file_size=2048
        ).as_(bot)
    elif kind == "photo":
        parent_kwargs["photo"] = [
            PhotoSize(file_id="PHO123", file_unique_id="UNIQ3", width=320, height=240).as_(bot)
        ]
    parent = Message(
        message_id=90,
        date=datetime.now(timezone.utc),
        chat=Chat(id=owner_id, type="private").as_(bot),
        from_user=user,
        caption=caption or None,
        **parent_kwargs,
    ).as_(bot)
    message = make_message(bot, text, owner_id, message_id=91, reply_to=parent)
    return Update(update_id=2, message=message)


def press(bot: Bot, data: str, owner_id: int, *, message_id: int = 42) -> Update:
    """A callback query pressing an inline button on a card."""
    from aiogram.types import CallbackQuery

    call = CallbackQuery(
        id="cb1",
        from_user=TgUser(id=owner_id, is_bot=False, first_name="Tester").as_(bot),
        chat_instance="1",
        data=data,
        message=make_message(bot, "card", owner_id, message_id=message_id),
    ).as_(bot)
    return Update(update_id=3, callback_query=call)


__all__ = ["Recorder", "attach", "make_message", "make_update", "press"]
