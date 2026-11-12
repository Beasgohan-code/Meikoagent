"""Safe declarative commands (Command Studio).

The webapp never sends code.  A declarative command is *data*:

    {
      "trigger": "/rules",
      "description": "Show the rules",
      "response": "Read the rules in the pinned message.",
      "buttons": [{"text": "Chat", "url": "https://t.me/x"}],
      "permission": "everyone",
      "scope": "all"
    }

This module validates that data and turns it into a real aiogram handler with
templating and permission checks.  No ``eval``, no ``exec``, no imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from meiko.logging_setup import get_logger
from meiko.security.permissions import Principal, Role, role_from_string
from meiko.ui import Btn, keyboard

log = get_logger("core.declarative")

TEMPLATE_RE = re.compile(r"\{([a-z_]{1,24})\}")
ALLOWED_VARIABLES = {"user", "username", "chat", "id", "date", "count", "bot"}
MAX_RESPONSE = 3500


class DeclarativeError(ValueError):
    """Invalid declarative command definition."""


@dataclass
class DeclarativeCommand:
    trigger: str
    response: str = ""
    description: str = ""
    permission: str = "everyone"
    scope: str = "all"
    buttons: list[dict[str, Any]] = field(default_factory=list)
    reply: bool = True
    delete_trigger: bool = False
    cooldown_seconds: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeclarativeCommand":
        command = cls(
            trigger=str(data.get("trigger", "")).strip().lstrip("/"),
            response=str(data.get("response", "")),
            description=str(data.get("description", ""))[:256],
            permission=str(data.get("permission", "everyone")),
            scope=str(data.get("scope", "all")),
            buttons=list(data.get("buttons") or []),
            reply=bool(data.get("reply", True)),
            delete_trigger=bool(data.get("delete_trigger", False)),
            cooldown_seconds=int(data.get("cooldown_seconds", 0) or 0),
        )
        command.validate()
        return command

    def validate(self) -> None:
        if not re.match(r"^[a-z0-9_]{1,32}$", self.trigger):
            raise DeclarativeError(
                "trigger must be lowercase letters, digits or underscores (max 32)"
            )
        if len(self.response) > MAX_RESPONSE:
            raise DeclarativeError(f"response is longer than {MAX_RESPONSE} characters")
        if self.scope not in ("all", "private", "group"):
            raise DeclarativeError("scope must be all, private or group")
        if self.permission not in {
            "everyone", "member", "trusted", "moderator", "admin", "bot_owner", "group_owner"
        }:
            raise DeclarativeError(f"unknown permission {self.permission!r}")
        unknown = set(TEMPLATE_RE.findall(self.response)) - ALLOWED_VARIABLES
        if unknown:
            raise DeclarativeError(f"unknown template variables: {', '.join(sorted(unknown))}")
        for button in self.buttons:
            if not isinstance(button, dict) or not button.get("text"):
                raise DeclarativeError("each button needs a text")
            if not (button.get("url") or button.get("callback_data")):
                raise DeclarativeError("each button needs a url or callback_data")
            if button.get("url") and not str(button["url"]).startswith(("https://", "http://", "tg://")):
                raise DeclarativeError("button url must be http(s) or tg://")


def render(template: str, message_obj: Message, bot_username: str | None = None) -> str:
    """Substitute the allowed variables – no expression evaluation."""
    from meiko.ui import esc, user_mention

    user = message_obj.from_user
    values = {
        "user": user_mention(user.id, user.first_name or "there") if user else "there",
        "username": (user.username or "") if user else "",
        "chat": esc(message_obj.chat.title or "this chat"),
        "id": str(user.id if user else ""),
        "date": message_obj.date.strftime("%Y-%m-%d") if message_obj.date else "",
        "count": "0",
        "bot": esc(bot_username or "MEIKO"),
    }
    def _replace(match: re.Match[str]) -> str:
        return values.get(match.group(1), match.group(0))
    return TEMPLATE_RE.sub(_replace, template)


def build_router(
    commands: list[DeclarativeCommand],
    *,
    bot_username: str | None = None,
    cache: Any | None = None,
) -> Router:
    """Create one router with a handler per declarative command."""
    router = Router(name="declarative")
    for command in commands:
        router.message.register(
            _make_handler(command, bot_username, cache),
            F.text.regexp(rf"^/{re.escape(command.trigger)}(?:@\w+)?(?:\s|$)"),
        )
    return router


def _make_handler(command: DeclarativeCommand, bot_username: str | None, cache: Any | None):
    async def handler(message: Message, principal: Principal | None = None) -> None:
        if principal is not None:
            required = role_from_string(command.permission)
            if required > Role.EVERYONE and not principal.has(required):
                await message.reply("You do not have permission to use this command.")
                return
        if cache is not None and command.cooldown_seconds:
            allowed, retry = await cache.rate_limit(
                f"decl:{command.trigger}:{message.chat.id}:{message.from_user.id if message.from_user else 0}",
                limit=1,
                window=command.cooldown_seconds,
            )
            if not allowed:
                await message.reply(f"Please wait {retry}s before using this again.")
                return
        text = render(command.response, message, bot_username)
        markup = None
        if command.buttons:
            rows = []
            for button in command.buttons:
                rows.append(
                    [
                        Btn(
                            str(button["text"]),
                            url=button.get("url"),
                            callback_data=button.get("callback_data"),
                            style=button.get("style"),
                        )
                    ]
                )
            markup = keyboard(rows, premium_icons=False)
        if command.reply:
            await message.reply(text, reply_markup=markup)
        else:
            await message.answer(text, reply_markup=markup)
        if command.delete_trigger:
            try:
                await message.delete()
            except Exception:
                log.debug("could not delete trigger message")

    handler.__name__ = f"declarative_{command.trigger}"
    return handler


async def load_commands(session, bot_id: int) -> list[DeclarativeCommand]:
    """Load enabled declarative commands for a bot from the database."""
    from sqlalchemy import select

    from meiko.db.models import BotCommandRecord

    rows = (
        await session.scalars(
            select(BotCommandRecord).where(
                BotCommandRecord.bot_id == bot_id,
                BotCommandRecord.handler_type == "declarative",
                BotCommandRecord.enabled.is_(True),
            )
        )
    ).all()
    commands: list[DeclarativeCommand] = []
    for row in rows:
        definition = dict(row.definition or {})
        definition.setdefault("trigger", row.name)
        definition.setdefault("description", row.description)
        definition.setdefault("permission", row.permission)
        definition.setdefault("scope", row.scope)
        try:
            commands.append(DeclarativeCommand.from_dict(definition))
        except DeclarativeError as exc:
            log.warning("skipping invalid declarative command %s: %s", row.name, exc)
    return commands
