"""Command registry + declarative command definitions.

Commands are declared once (name, aliases, permission, scope, arguments) and
consumed by three things: the aiogram router, ``setMyCommands``
synchronisation and the webapp Command Studio.  That keeps Telegram's command
menu, the bot handlers and the UI perfectly aligned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from meiko.security.permissions import Role, role_from_string

Handler = Callable[..., Awaitable[Any]]

COMMAND_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
ARGUMENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


class CommandError(ValueError):
    """Invalid command declaration."""


@dataclass(frozen=True)
class CommandArgument:
    name: str
    required: bool = False
    kind: str = "string"  # string/int/float/duration/user/choice
    description: str = ""
    choices: tuple[str, ...] = ()

    def validate(self) -> None:
        if not ARGUMENT_NAME_RE.match(self.name):
            raise CommandError(f"invalid argument name {self.name!r}")


@dataclass
class Command:
    name: str
    description: str = ""
    usage: str = ""
    aliases: list[str] = field(default_factory=list)
    category: str = "general"
    permission: Role = Role.EVERYONE
    scope: str = "all"  # all | private | group
    arguments: list[CommandArgument] = field(default_factory=list)
    handler: Handler | None = None
    plugin: str | None = None
    hidden: bool = False
    admin_only: bool = False

    def validate(self) -> None:
        if not COMMAND_NAME_RE.match(self.name):
            raise CommandError(f"invalid command name {self.name!r}")
        for alias in self.aliases:
            if not COMMAND_NAME_RE.match(alias):
                raise CommandError(f"invalid alias {alias!r} for /{self.name}")
        if self.scope not in ("all", "private", "group"):
            raise CommandError(f"invalid scope {self.scope!r} for /{self.name}")
        for argument in self.arguments:
            argument.validate()

    @property
    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]

    def telegram_entry(self) -> dict[str, str]:
        return {"command": self.name, "description": self.description[:256]}


class CommandRegistry:
    """Process wide registry; plugins register/unregister as they start/stop."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command, *, override: bool = False) -> None:
        command.validate()
        for name in command.all_names:
            if name in self._commands and not override and self._commands[name] is not command:
                owner = self._commands[name].plugin or "core"
                raise CommandError(f"command /{name} already registered by {owner}")
        for name in command.all_names:
            self._commands[name] = command

    def unregister(self, name: str) -> None:
        command = self._commands.get(name)
        if command is None:
            return
        for alias in command.all_names:
            self._commands.pop(alias, None)

    def unregister_plugin(self, plugin_slug: str) -> int:
        removed = [c for c in self.unique() if c.plugin == plugin_slug]
        for command in removed:
            self.unregister(command.name)
        return len(removed)

    def get(self, name: str) -> Command | None:
        return self._commands.get(name.lstrip("/").split("@")[0].lower())

    def unique(self) -> list[Command]:
        seen: dict[int, Command] = {}
        for command in self._commands.values():
            seen[id(command)] = command
        return sorted(seen.values(), key=lambda c: c.name)

    def by_plugin(self, plugin_slug: str) -> list[Command]:
        return [c for c in self.unique() if c.plugin == plugin_slug]

    def by_category(self, category: str) -> list[Command]:
        return [c for c in self.unique() if c.category == category]

    def telegram_menu(self, scope: str = "all") -> list[dict[str, str]]:
        return [
            c.telegram_entry()
            for c in self.unique()
            if not c.hidden and not c.admin_only and scope in (c.scope, "all")
        ]

    def categories(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for command in self.unique():
            counts[command.category] = counts.get(command.category, 0) + 1
        return counts

    def clear(self) -> None:
        self._commands.clear()

    def __len__(self) -> int:
        return len(self.unique())


#: Global registry instance (intentionally explicit, not module import side effects).
registry = CommandRegistry()


def command(
    name: str,
    *,
    description: str = "",
    aliases: Iterable[str] = (),
    category: str = "general",
    permission: Role | str = Role.EVERYONE,
    scope: str = "all",
    usage: str = "",
    arguments: Iterable[CommandArgument] = (),
    admin_only: bool = False,
) -> Callable[[Handler], Handler]:
    """Decorator registering an aiogram handler in the command registry."""

    def decorator(func: Handler) -> Handler:
        cmd = Command(
            name=name,
            description=description,
            aliases=list(aliases),
            category=category,
            permission=permission if isinstance(permission, Role) else role_from_string(permission),
            scope=scope,
            usage=usage,
            arguments=list(arguments),
            handler=func,
            admin_only=admin_only or permission in ("admin", "bot_owner"),
        )
        cmd.validate()
        registry.register(cmd, override=True)
        setattr(func, "__meiko_command__", cmd)
        return func

    return decorator


def sync_from_modules(*modules: Any) -> int:
    """Import modules so their ``@command`` decorators run; returns count."""
    return len(registry)
