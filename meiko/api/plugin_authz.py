"""One place that answers "may this caller touch this plugin installation?".

The API used to answer that question inline, route by route, with the same
sentence repeated nine times::

    if record.bot_id is not None:
        await BotService(session).get(record.bot_id, auth.user.id)

The ``if`` is the bug.  When ``bot_id`` was ``None`` the check did not run, so an
installation in *global* scope – the scope the platform owner alone may create –
was correctly owner-only by accident of being skipped, and any authenticated
user could install, enable, configure, restart, read logs from or delete it.

This module replaces the scattered checks with two decisions:

``require_scope(auth, bot_id=…, group_id=…)``
    may this caller *create* an installation in that scope?

``authorize_installation(auth, session, record)``
    may this caller *touch* this existing row?

Both raise :class:`HTTPException` with a stable error code, and every refusal
path for another tenant's row is a plain ``404 not_found`` – identical to a row
that does not exist – so ids cannot be probed across tenants.

The rules, in the order they are applied:

============================  ===========================================  =======
caller                        global scope                                 bot/group scope
============================  ===========================================  =======
platform owner                allowed (when the feature flag is on)         allowed
owner of the bot             refused – *never* global                     allowed
anybody else                 refused                                      refused (404)
============================  ===========================================  =======

Group scope additionally requires the group row to belong to the same bot, so a
group id cannot be borrowed to reach a bot the caller does not own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from meiko.api.deps import AuthContext, error
from meiko.config import get_settings
from meiko.db.models import BotAccount, BotGroup, PluginInstallation
from meiko.services.core import BotService, NotFound

#: ``scope`` values that are owned by a bot (and therefore by that bot's owner).
BOT_SCOPES = ("bot", "group")


@dataclass(frozen=True)
class ScopeGrant:
    """The resolved scope a caller is allowed to act in."""

    bot_id: int | None = None
    group_id: int | None = None
    is_global: bool = False

    @property
    def scope(self) -> str:
        if self.group_id is not None:
            return "group"
        if self.bot_id is not None:
            return "bot"
        return "global"


def is_platform_owner(auth: AuthContext) -> bool:
    return bool(auth.principal.is_platform_owner)


def global_scope_enabled() -> bool:
    """Owners can switch global-scope installations off entirely (secure default)."""
    return bool(getattr(get_settings(), "plugin_global_scope_enabled", True))


async def require_scope(
    auth: AuthContext,
    session: AsyncSession,
    *,
    bot_id: int | None,
    group_id: int | None = None,
) -> ScopeGrant:
    """Authorize *creating* an installation in ``(bot_id, group_id)``.

    ``bot_id=None`` with no ``group_id`` means global scope, which is reserved
    for the platform owner.  Everything else requires ownership of the bot – and
    of the group, when one is named.
    """
    if group_id is not None and bot_id is None:
        # A group always belongs to a bot; a group id without a bot id would let
        # the caller pick any bot whose group id matches.
        raise error(
            422,
            "bot_required",
            "group scope requires the bot the group belongs to",
        )

    if bot_id is None:
        if not is_platform_owner(auth):
            raise error(
                403,
                "global_scope_forbidden",
                "global plugin installations are a platform-owner action",
            )
        if not global_scope_enabled():
            raise error(
                403,
                "global_scope_disabled",
                "global plugin installations are disabled on this platform",
            )
        return ScopeGrant(is_global=True)

    if not is_platform_owner(auth):
        try:
            await BotService(session).get(bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "bot not found") from exc
    else:
        try:
            await BotService(session).get(bot_id)
        except NotFound as exc:
            raise error(404, "not_found", "bot not found") from exc

    if group_id is not None:
        group = await session.get(BotGroup, group_id)
        if group is None or group.bot_id != bot_id:
            raise error(404, "not_found", "group not found")
    return ScopeGrant(bot_id=bot_id, group_id=group_id)


async def installation_bot_id(
    session: AsyncSession, record: PluginInstallation
) -> int | None:
    """The bot an installation belongs to, resolving group rows through the group."""
    if record.bot_id is not None:
        return record.bot_id
    if record.group_id is not None:
        group = await session.get(BotGroup, record.group_id)
        return group.bot_id if group is not None else None
    return None


async def can_manage_installation(
    auth: AuthContext, session: AsyncSession, record: PluginInstallation
) -> bool:
    """Boolean form of :func:`authorize_installation`, for read-only filtering."""
    if is_platform_owner(auth):
        return True
    bot_id = await installation_bot_id(session, record)
    if bot_id is None:
        return False  # global row, non-owner
    bot = await session.get(BotAccount, bot_id)
    return bot is not None and bot.owner_id == auth.user.id


async def authorize_installation(
    auth: AuthContext, session: AsyncSession, record: PluginInstallation | None
) -> PluginInstallation:
    """Authorize acting on an existing installation.

    Every failure is a ``404 not_found`` – including the global-row case, so that
    a stranger cannot tell "this id is a global installation" apart from "this id
    does not exist".  The reason is carried in the message for logs, not for the
    client to enumerate.
    """
    if record is None:
        raise error(404, "not_found", "installation not found")
    if is_platform_owner(auth):
        return record
    bot_id = await installation_bot_id(session, record)
    if bot_id is None:
        raise error(404, "not_found", "installation not found")
    bot = await session.get(BotAccount, bot_id)
    if bot is None or bot.owner_id != auth.user.id:
        raise error(404, "not_found", "installation not found")
    return record


def owner_metadata(auth: AuthContext, **extra: Any) -> dict[str, Any]:
    """Audit metadata that records *which* authority allowed an owner action."""
    payload: dict[str, Any] = {"actor_role": auth.role.name.lower(), "via": auth.via}
    payload.update(extra)
    return payload
