"""Resolve the owner of a plugin scope.

Plugins talk to Telegram through ``ctx.bot`` and to data through ``ctx.session()``;
neither exposes "who owns this bot".  Several features need it (code-execution
gates, keep-alive alerts), so the lookup lives here once and is cached per
context for the duration of one call.
"""

from __future__ import annotations

from typing import Any

from meiko.db.models import BotAccount, User


async def scope_owner(context: Any) -> tuple[int | None, bool]:
    """Return ``(owner_telegram_id, is_platform_owner)`` for a plugin context.

    Never raises: a missing bot/user simply yields ``(None, False)`` so callers
    can degrade (e.g. log instead of alerting).
    """
    bot_id = getattr(context, "bot_id", None)
    if bot_id is None:
        return None, False
    try:
        async with context.session() as session:
            bot = await session.get(BotAccount, bot_id)
            if bot is None:
                return None, False
            owner = await session.get(User, bot.owner_id)
            if owner is None:
                return None, False
            return owner.telegram_id, bool(owner.is_owner)
    except Exception:  # pragma: no cover - database unavailable
        return None, False


__all__ = ["scope_owner"]
