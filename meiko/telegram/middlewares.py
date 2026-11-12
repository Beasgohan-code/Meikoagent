"""aiogram middlewares: context injection, tenancy, permissions and metrics."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User
from sqlalchemy import text

from meiko.db.models import BotAccount

from meiko.cache import get_cache
from meiko.db.base import session_scope
from meiko.logging_setup import get_logger
from meiko.security.permissions import (
    Principal,
    Role,
    TELEGRAM_STATUS_ROLE,
    highest,
    role_from_string,
)
from meiko.services.core import ActivityEventService, BotService, GroupService, UserService

log = get_logger("telegram.middlewares")


@dataclass
class BotRuntimeContext:
    """Everything handlers need, injected once per update."""

    bot_id: int
    owner_user_id: int
    bot_username: str | None = None
    dispatcher_id: str = ""
    started_at: float = field(default_factory=time.time)
    counters: dict[str, int] = field(default_factory=dict)

    def count(self, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1


class ContextMiddleware(BaseMiddleware):
    """Attaches the runtime context and a per-update id."""

    def __init__(self, context: BotRuntimeContext, platform_owner_ids: set[int] | None = None) -> None:
        self.context = context
        self.platform_owner_ids = platform_owner_ids or set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update_id = uuid.uuid4().hex[:12]
        data["meiko"] = self.context
        data["request_id"] = update_id
        self.context.count("updates")
        started = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            if elapsed > 1500:
                log.warning(
                    "slow update %s handled in %.0fms", type(event).__name__, elapsed
                )


class RateLimitMiddleware(BaseMiddleware):
    """Per-user update rate limit to protect the bot from floods."""

    def __init__(self, limit: int = 20, window: int = 10) -> None:
        self.limit = limit
        self.window = window

    async def __call__(self, handler, event, data):
        user: User | None = data.get("event_from_user")
        if user is not None and not user.is_bot:
            allowed, _ = await get_cache().rate_limit(
                f"update:{user.id}", limit=self.limit, window=self.window
            )
            if not allowed:
                log.debug("rate limited update from %s", user.id)
                return None
        return await handler(event, data)


class TenantMiddleware(BaseMiddleware):
    """Loads the bot/user/group rows and derives the caller's :class:`Principal`."""

    def __init__(self, platform_owner_ids: set[int] | None = None) -> None:
        self.platform_owner_ids = platform_owner_ids or set()

    async def __call__(self, handler, event, data):
        ctx: BotRuntimeContext | None = data.get("meiko")
        user: User | None = data.get("event_from_user")
        if ctx is None:
            return await handler(event, data)
        async with session_scope() as session:
            bot = await session.get(BotAccount, ctx.bot_id)
            owner_id = bot.owner_id if bot else ctx.owner_user_id
            if user is not None:
                platform_user = await UserService(session).get_or_create_telegram_user(
                    user.id,
                    username=user.username,
                    first_name=user.first_name or "",
                    last_name=user.last_name or "",
                    language_code=user.language_code or "en",
                )
                data["platform_user"] = platform_user
            chat = getattr(event, "chat", None) or getattr(
                getattr(event, "message", None), "chat", None
            )
            roles: list[Role] = [Role.MEMBER if user else Role.EVERYONE]
            is_bot_owner = bool(user and user.id == owner_id)
            is_platform_owner = bool(user and user.id in self.platform_owner_ids)
            if is_bot_owner:
                roles.append(Role.BOT_OWNER)
            if is_platform_owner:
                roles.append(Role.OWNER)
            if platform_user is not None:
                roles.append(role_from_string(platform_user.role))
            if chat is not None and getattr(chat, "type", "") in ("group", "supergroup"):
                status = getattr(event, "new_chat_member", None)
                if status is not None:
                    roles.append(TELEGRAM_STATUS_ROLE.get(status.status, Role.MEMBER))
            data["principal"] = Principal(
                user_id=user.id if user else 0,
                role=highest(roles),
                bot_id=ctx.bot_id,
                is_bot_owner=is_bot_owner,
                is_platform_owner=is_platform_owner,
            )
        return await handler(event, data)


class ActivityMiddleware(BaseMiddleware):
    """Writes a lightweight activity record for every handled message."""

    def __init__(self, sample_every: int = 5) -> None:
        self._counter = 0
        self.sample_every = sample_every

    async def __call__(self, handler, event, data):
        ctx: BotRuntimeContext | None = data.get("meiko")
        result = await handler(event, data)
        if ctx is None or not isinstance(event, Message):
            return result
        self._counter += 1
        if self._counter % self.sample_every:
            return result
        try:
            async with session_scope() as session:
                await ActivityEventService(session).record(
                    bot_id=ctx.bot_id,
                    kind="message.received",
                    title=(event.text or event.caption or "media")[:80],
                    telegram_user_id=event.from_user.id if event.from_user else None,
                    data={"chat_id": event.chat.id},
                )
        except Exception:  # pragma: no cover - telemetry must never break handling
            log.debug("activity record failed", exc_info=True)
        return result


class GroupRegistryMiddleware(BaseMiddleware):
    """Keeps ``bot_groups`` up to date from real group activity."""

    async def __call__(self, handler, event, data):
        ctx: BotRuntimeContext | None = data.get("meiko")
        chat = getattr(event, "chat", None)
        if ctx is not None and chat is not None and chat.type in ("group", "supergroup"):
            try:
                async with session_scope() as session:
                    await GroupService(session).upsert(
                        ctx.bot_id,
                        chat.id,
                        title=chat.title or "",
                        username=chat.username,
                        chat_type=chat.type,
                    )
            except Exception:  # pragma: no cover
                log.debug("group upsert failed", exc_info=True)
        return await handler(event, data)


class CallbackAnswerMiddleware(BaseMiddleware):
    """Always answer callback queries so clients never hang on a spinner."""

    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        finally:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:  # pragma: no cover - already answered/expired
                    log.debug("callback answer failed", exc_info=True)


class ErrorMiddleware(BaseMiddleware):
    """Catches handler exceptions, logs them and notifies the bot owner once."""

    def __init__(self, notify_owner: bool = True) -> None:
        self.notify_owner = notify_owner

    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("handler error on %s: %s", type(event).__name__, exc)
            ctx: BotRuntimeContext | None = data.get("meiko")
            bot = data.get("bot")
            if ctx is not None and bot is not None:
                try:
                    async with session_scope() as session:
                        await session.execute(
                            text("UPDATE bots SET last_error = :err WHERE id = :id"),
                            {"err": f"{type(exc).__name__}: {exc}"[:500], "id": ctx.bot_id},
                        )
                    if self.notify_owner:
                        await bot.send_message(
                            ctx.owner_user_id,
                            "⚠️ <b>Handler error</b>\n"
                            f"<code>{type(exc).__name__}: {str(exc)[:300]}</code>",
                        )
                except Exception:  # pragma: no cover
                    log.debug("error notification failed", exc_info=True)
            return None
