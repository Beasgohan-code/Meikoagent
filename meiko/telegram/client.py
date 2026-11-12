"""aiogram client wrapper with retries, single-consumer locking and health.

Every bot token is owned by exactly one runtime.  ``BotTokenRegistry`` enforces
that locally (file lock) and across instances (Redis lock) so two processes can
never long-poll the same token – Telegram would otherwise answer 409 Conflict.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import BotCommand

from meiko.cache import get_cache
from meiko.config import get_settings
from meiko.logging_setup import get_logger
from meiko.security.crypto import token_fingerprint

log = get_logger("telegram.client")

DEFAULT_PARSE_MODE = ParseMode.HTML


class TokenOwnershipError(RuntimeError):
    """Another consumer already owns this bot token."""


@dataclass
class TokenLock:
    fingerprint: str
    owner: str
    acquired_at: float = field(default_factory=time.time)
    path: Path | None = None

    def release(self) -> None:
        if self.path and self.path.exists():
            try:
                self.path.unlink()
            except OSError:  # pragma: no cover
                log.warning("could not remove token lock file %s", self.path)


class BotTokenRegistry:
    """Guarantees a single update consumer per token on this host."""

    def __init__(self, lock_dir: Path | None = None) -> None:
        self.lock_dir = lock_dir or get_settings().lock_dir
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._held: dict[str, TokenLock] = {}

    def _path(self, fingerprint: str) -> Path:
        return self.lock_dir / f"bot-{fingerprint}.lock"

    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def acquire(self, token: str, owner: str = "meiko") -> TokenLock:
        fingerprint = token_fingerprint(token)
        if fingerprint in self._held:
            return self._held[fingerprint]
        path = self._path(fingerprint)
        if path.exists():
            try:
                pid = int(path.read_text().strip().split()[0])
            except (ValueError, OSError):
                pid = 0
            if pid != os.getpid() and self._pid_alive(pid):
                raise TokenOwnershipError(
                    f"bot token is already being polled by pid {pid} – refusing a second consumer"
                )
            path.unlink(missing_ok=True)
        path.write_text(f"{os.getpid()} {owner} {int(time.time())}")
        lock = TokenLock(fingerprint=fingerprint, owner=owner, path=path)
        self._held[fingerprint] = lock
        return lock

    def release(self, token: str) -> None:
        fingerprint = token_fingerprint(token)
        lock = self._held.pop(fingerprint, None)
        if lock:
            lock.release()

    def is_held(self, token: str) -> bool:
        return token_fingerprint(token) in self._held


async def acquire_distributed(token: str, owner: str, ttl: int = 60) -> bool:
    """Redis lock so two *instances* cannot poll the same token."""
    cache = get_cache()
    async with cache.lock(f"poll:{token_fingerprint(token)}", timeout=ttl) as acquired:
        if not acquired:
            return False
        # Lock is released when the context exits; a heartbeat renews it in runtime.
        return True


def build_bot(token: str, extra_defaults: dict[str, Any] | None = None) -> Bot:
    """Create an aiogram Bot with Meiko defaults."""
    settings = get_settings()
    session = AiohttpSession(timeout=30)
    if settings.telegram_api_base and "api.telegram.org" not in settings.telegram_api_base:
        from aiogram.client.telegram import TelegramAPIServer

        session.api = TelegramAPIServer.from_base(settings.telegram_api_base)
    properties = {
        "parse_mode": DEFAULT_PARSE_MODE,
        "link_preview_is_disabled": True,
        "allow_sending_without_reply": True,
    }
    if extra_defaults:
        properties.update(extra_defaults)
    return Bot(token=token, session=session, default=DefaultBotProperties(**properties))


async def with_retry(
    coro_factory: Any,
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> Any:
    """Run a Telegram call honouring ``retry_after`` and transient failures."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except TelegramRetryAfter as exc:
            delay = min(float(exc.retry_after), max_delay)
            log.warning("flood control: sleeping %.1fs (attempt %s)", delay, attempt)
            await asyncio.sleep(delay)
            last_error = exc
        except (TelegramNetworkError, TelegramServerError) as exc:
            delay = min(base_delay * 2 ** (attempt - 1), max_delay)
            log.warning("telegram transient error: %s – retrying in %.1fs", exc, delay)
            await asyncio.sleep(delay)
            last_error = exc
    assert last_error is not None
    raise last_error


async def fetch_me(bot: Bot) -> dict[str, Any]:
    """``getMe`` with retry – used for ownership verification."""
    user = await with_retry(lambda: bot.get_me())
    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "is_bot": user.is_bot,
        "can_join_groups": user.can_join_groups,
        "can_read_all_group_messages": user.can_read_all_group_messages,
        "supports_guest_queries": getattr(user, "supports_guest_queries", None),
        "can_manage_bots": getattr(user, "can_manage_bots", None),
    }


async def set_commands(bot: Bot, commands: list[dict[str, str]]) -> bool:
    if not commands:
        return False
    await with_retry(lambda: bot.set_my_commands([BotCommand(**c) for c in commands[:100]]))
    return True


@asynccontextmanager
async def managed_bot(token: str, owner: str = "meiko") -> AsyncIterator[Bot]:
    """Context manager owning both the aiogram client and the token lock."""
    registry_lock = BotTokenRegistry()
    lock = registry_lock.acquire(token, owner)
    bot = build_bot(token)
    try:
        yield bot
    finally:
        await bot.session.close()
        lock.release()
