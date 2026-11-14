"""Permission model shared by the bot, the API and plugins.

One source of truth for "who may do what", so a bot handler and a REST call
cannot disagree.  Roles are ordered – higher values inherit lower privileges.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable


class Role(IntEnum):
    EVERYONE = 0
    MEMBER = 10
    TRUSTED = 20
    MODERATOR = 30
    ADMIN = 40
    GROUP_OWNER = 50
    BOT_OWNER = 60
    PLATFORM_ADMIN = 80
    OWNER = 100


ROLE_NAMES: dict[str, Role] = {
    "everyone": Role.EVERYONE,
    "member": Role.MEMBER,
    "trusted": Role.TRUSTED,
    "moderator": Role.MODERATOR,
    "admin": Role.ADMIN,
    "group_owner": Role.GROUP_OWNER,
    "bot_owner": Role.BOT_OWNER,
    "platform_admin": Role.PLATFORM_ADMIN,
    "owner": Role.OWNER,
}

#: Capabilities required by command/plugin declarations.
CAPABILITIES: dict[str, Role] = {
    "moderation.warn": Role.MODERATOR,
    "moderation.mute": Role.MODERATOR,
    "moderation.kick": Role.MODERATOR,
    "moderation.ban": Role.ADMIN,
    "moderation.purge": Role.ADMIN,
    "moderation.settings": Role.ADMIN,
    "moderation.reports": Role.MEMBER,
    "plugins.manage": Role.BOT_OWNER,
    "plugins.configure": Role.ADMIN,
    "bots.manage": Role.BOT_OWNER,
    "bots.runtime": Role.BOT_OWNER,
    "automation.manage": Role.ADMIN,
    "commands.manage": Role.ADMIN,
    "api.keys": Role.BOT_OWNER,
    "platform.admin": Role.PLATFORM_ADMIN,
    "platform.owner": Role.OWNER,
}


@dataclass(frozen=True)
class Principal:
    """An authenticated actor (Telegram command sender or web user)."""

    user_id: int
    role: Role = Role.EVERYONE
    bot_id: int | None = None
    group_id: int | None = None
    is_bot_owner: bool = False
    is_platform_owner: bool = False
    scopes: frozenset[str] = frozenset()

    def has(self, required: Role | str) -> bool:
        if self.is_platform_owner:
            return True
        needed = ROLE_NAMES.get(required, Role.EVERYONE) if isinstance(required, str) else required
        if needed == Role.OWNER:
            return self.is_platform_owner
        if needed > Role.BOT_OWNER and not self.is_platform_owner:
            return False
        return self.role >= needed

    def can(self, capability: str) -> bool:
        if self.is_platform_owner:
            return True
        required = CAPABILITIES.get(capability, Role.EVERYONE)
        return self.has(required)

    def with_role(self, role: Role) -> "Principal":
        return Principal(
            user_id=self.user_id,
            role=role,
            bot_id=self.bot_id,
            group_id=self.group_id,
            is_bot_owner=self.is_bot_owner,
            is_platform_owner=self.is_platform_owner,
            scopes=self.scopes,
        )


class PermissionDenied(PermissionError):
    """Raised when a principal lacks the required role/capability."""

    def __init__(self, capability: str, principal: Principal) -> None:
        super().__init__(f"permission denied for {capability}")
        self.capability = capability
        self.principal = principal


def require(principal: Principal, capability: str) -> None:
    if not principal.can(capability):
        raise PermissionDenied(capability, principal)


def role_from_string(value: str | None) -> Role:
    return ROLE_NAMES.get((value or "").lower(), Role.EVERYONE)


def highest(roles: Iterable[Role]) -> Role:
    return max(roles, default=Role.EVERYONE)


#: Telegram chat-member status -> Meiko role (for group commands).
TELEGRAM_STATUS_ROLE = {
    "creator": Role.GROUP_OWNER,
    "administrator": Role.ADMIN,
    "member": Role.MEMBER,
    "restricted": Role.MEMBER,
    "left": Role.EVERYONE,
    "kicked": Role.EVERYONE,
}
