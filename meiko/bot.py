"""Meiko bot application: dispatcher assembly, polling and webhook modes.

One :class:`BotApplication` is one update consumer for exactly one token.  It
owns the dispatcher, middlewares, core routers, the plugin manager and its
lifecycle.  Clone workers create additional applications; the token registry
guarantees a token is never consumed twice.

Webhook mode feeds raw updates through :func:`BotApplication.feed_update` so the
API service can receive Telegram webhooks; widget updates (Bot API 10.4), which
aiogram does not know yet, are extracted *before* dispatch and routed to the
plugin/automation layer.
"""

from __future__ import annotations

import asyncio
import signal
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramConflictError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, Update

from meiko.cache import get_cache
from meiko.config import get_settings
from meiko.core.commands import registry as command_registry
from meiko.db.base import session_scope
from meiko.db.models import BotAccount, PluginInstallation
from meiko.logging_setup import get_logger
from meiko.plugins.base import registry as plugin_registry
from meiko.plugins.context import PluginScope
from meiko.plugins.manager import PluginManager
from meiko.services.core import BotService, PluginService
from meiko.telegram.api.adapter import EXTRA_ALLOWED_UPDATES, extract_widget_update
from meiko.telegram.client import (
    BotTokenRegistry,
    TokenOwnershipError,
    build_bot,
    fetch_me,
    set_commands,
    with_retry,
)
from meiko.telegram.middlewares import (
    ActivityMiddleware,
    BotRuntimeContext,
    CallbackAnswerMiddleware,
    ContextMiddleware,
    ErrorMiddleware,
    GroupRegistryMiddleware,
    RateLimitMiddleware,
    TenantMiddleware,
)
from meiko.telegram.routers import core as core_router
from meiko.telegram.routers import moderation as moderation_router
from meiko.telegram.routers import owner as owner_router

log = get_logger("bot")

WidgetHandler = Callable[[Any], Awaitable[None]]


@dataclass
class BotApplication:
    bot_id: int
    owner_id: int
    token: str
    username: str | None = None
    runtime_mode: str = "polling"
    plugins: PluginManager | None = None
    bot: Bot | None = None
    dispatcher: Dispatcher | None = None
    context: BotRuntimeContext | None = None
    widget_handlers: list[WidgetHandler] = field(default_factory=list)
    _polling_task: asyncio.Task[Any] | None = None
    _stopping: asyncio.Event = field(default_factory=asyncio.Event)
    _lock_registry: BotTokenRegistry | None = None
    started_at: float = 0.0
    healthy: bool = False
    last_error: str | None = None

    # -- assembly --------------------------------------------------------
    def build_dispatcher(self) -> Dispatcher:
        settings = get_settings()
        dispatcher = Dispatcher(storage=MemoryStorage())
        ctx = BotRuntimeContext(
            bot_id=self.bot_id,
            owner_user_id=self.owner_id,
            bot_username=self.username,
            dispatcher_id=f"bot-{self.bot_id}",
        )
        self.context = ctx
        owners = settings.owner_ids

        for observer in (dispatcher.message, dispatcher.callback_query, dispatcher.chat_member,
                         dispatcher.inline_query, dispatcher.my_chat_member):
            observer.middleware(ContextMiddleware(ctx, owners))
            observer.middleware(ErrorMiddleware(notify_owner=True))
            observer.middleware(TenantMiddleware(owners))
            observer.middleware(GroupRegistryMiddleware())
            observer.middleware(CallbackAnswerMiddleware())
        dispatcher.message.middleware(ActivityMiddleware())
        dispatcher.message.middleware(RateLimitMiddleware(limit=30, window=10))

        dispatcher.include_router(core_router.router)
        dispatcher.include_router(moderation_router.router)
        dispatcher.include_router(owner_router.router)
        self.dispatcher = dispatcher
        return dispatcher

    async def load_plugins(self) -> dict[str, str]:
        """Bring the runtime in sync with the database plugin states."""
        if self.plugins is None:
            self.plugins = PluginManager()
        async with session_scope() as session:
            installs = await PluginService(session).installations_for_bot(self.bot_id)
            snapshot = list(installs)
            for install in snapshot:
                session.expunge(install)
        result = await self.plugins.reconcile(
            bot_id=self.bot_id,
            group_id=None,
            installs=snapshot,
            bot=self.bot,
            dispatcher=self.dispatcher,
        )
        log.info("bot %s plugin state: %s", self.bot_id, result)
        return result

    # -- lifecycle -------------------------------------------------------
    async def start_polling(self) -> None:
        self.build_dispatcher()
        self.bot = build_bot(self.token)
        self._lock_registry = BotTokenRegistry()
        self._lock_registry.acquire(self.token, owner=f"bot:{self.bot_id}")
        me = await fetch_me(self.bot)
        self.username = me.get("username")
        if self.context:
            self.context.bot_username = self.username
        await self.load_plugins()
        await set_commands(self.bot, command_registry.telegram_menu())
        self.started_at = time.time()
        self.healthy = True
        allowed = list(_default_allowed_updates())
        self._polling_task = asyncio.create_task(
            self._poll_loop(allowed), name=f"poll:{self.bot_id}"
        )
        log.info("bot %s (@%s) polling started", self.bot_id, self.username)

    async def _poll_loop(self, allowed: list[str]) -> None:
        assert self.bot is not None
        backoff = 1.0
        while not self._stopping.is_set():
            try:
                await self.bot.delete_webhook(drop_pending_updates=False)
                await self.dispatcher.start_polling(  # type: ignore[union-attr]
                    self.bot,
                    allowed_updates=allowed,
                    handle_signals=False,
                    close_bot_session=False,
                )
                return
            except TelegramConflictError as exc:
                self.healthy = False
                self.last_error = str(exc)
                log.error("polling conflict for bot %s: %s", self.bot_id, exc)
                return
            except TelegramUnauthorizedError as exc:
                self.healthy = False
                self.last_error = "invalid bot token"
                log.error("bot %s token rejected: %s", self.bot_id, exc)
                return
            except asyncio.CancelledError:
                return
            except Exception as exc:  # pragma: no cover - network resilience
                self.last_error = str(exc)
                log.warning("polling error for bot %s: %s (retry in %.0fs)", self.bot_id, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def feed_update(self, payload: dict[str, Any]) -> None:
        """Webhook entry point: widget updates first, then the aiogram dispatcher."""
        widget_update = extract_widget_update(payload)
        if widget_update is not None:
            await self.handle_widget_update(widget_update)
        if self.bot is None or self.dispatcher is None:
            return
        try:
            update = Update.model_validate(payload, context={"bot": self.bot})
        except Exception as exc:
            log.warning("could not parse update for bot %s: %s", self.bot_id, exc)
            return
        await self.dispatcher.feed_update(self.bot, update)

    async def handle_widget_update(self, widget_update: Any) -> None:
        for handler in list(self.widget_handlers):
            try:
                await handler(widget_update)
            except Exception:  # pragma: no cover
                log.exception("widget handler failed for bot %s", self.bot_id)

    async def stop(self) -> None:
        self._stopping.set()
        self.healthy = False
        if self.plugins is not None:
            await self.plugins.stop_all(self.dispatcher)
        if self._polling_task is not None:
            self._polling_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._polling_task
        if self.dispatcher is not None:
            with suppress(Exception):
                await self.dispatcher.stop_polling()
        if self.bot is not None:
            with suppress(Exception):
                await self.bot.session.close()
        if self._lock_registry is not None:
            self._lock_registry.release(self.token)
        log.info("bot %s stopped", self.bot_id)

    # -- health ----------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "status": "running" if self.healthy else "stopped",
            "mode": self.runtime_mode,
            "username": self.username,
            "uptime_seconds": int(time.time() - self.started_at) if self.started_at else 0,
            "plugins": self.plugins.running_summary() if self.plugins else [],
            "commands": len(command_registry),
            "polling_task_alive": bool(self._polling_task and not self._polling_task.done()),
            "last_error": self.last_error,
            "counters": self.context.counters if self.context else {},
        }


def _default_allowed_updates() -> tuple[str, ...]:
    return (
        "message",
        "edited_message",
        "channel_post",
        "callback_query",
        "inline_query",
        "chat_member",
        "my_chat_member",
        "chat_join_request",
        "message_reaction",
        "message_reaction_count",
        "guest_message",
        "managed_bot",
        "poll",
        "poll_answer",
        *EXTRA_ALLOWED_UPDATES,
    )


async def load_bot_application(bot_id: int) -> BotApplication:
    """Build an application from the database (never from user supplied input)."""
    async with session_scope() as session:
        bot_row = await session.get(BotAccount, bot_id)
        if bot_row is None:
            raise LookupError(f"bot {bot_id} not found")
        token = BotService(session).token(bot_row)
        owner_id = bot_row.owner_id
        username = bot_row.username
        runtime_mode = bot_row.runtime_mode
    return BotApplication(
        bot_id=bot_id,
        owner_id=owner_id,
        token=token,
        username=username,
        runtime_mode=runtime_mode,
    )


async def run_bot_forever(bot_id: int) -> None:
    """Standalone runner used by ``python start.py --service bot``."""
    from meiko.db.base import init_engine
    from meiko.logging_setup import setup_logging

    setup_logging("bot")
    init_engine()
    application = await load_bot_application(bot_id)
    await application.start_polling()
    stop_event = asyncio.Event()

    def _signal(*_: Any) -> None:
        log.info("shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal)
    try:
        await stop_event.wait()
    finally:
        await application.stop()
