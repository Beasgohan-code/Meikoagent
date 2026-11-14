"""Command Studio: registry + safe declarative commands + Telegram sync."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import select

from meiko.api.deps import CurrentAuth, SessionDep, error
from meiko.api.schemas import CommandRequest, CommandResponse
from meiko.core.commands import registry as command_registry
from meiko.core.commands import seed_core_commands

# The Studio must describe the surface that exists, even before any bot starts.
seed_core_commands()
from meiko.core.declarative import DeclarativeCommand, DeclarativeError
from meiko.db.models import BotCommandRecord, CommandExecution, utcnow
from meiko.security.permissions import Role, role_from_string
from meiko.services.core import AuditService, BotService, NotFound

router = APIRouter(prefix="/bots/{bot_id}/commands", tags=["commands"])


async def _owned_bot(session, auth, bot_id: int):
    try:
        return await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc


async def _overrides(session, bot_id: int) -> dict[str, BotCommandRecord]:
    """Per-bot rows keyed by command name, so a builtin can be re-described."""
    rows = (
        await session.scalars(
            select(BotCommandRecord).where(BotCommandRecord.bot_id == bot_id)
        )
    ).all()
    return {row.name: row for row in rows}


@router.get("", response_model=list[dict])
async def list_commands(
    bot_id: int,
    auth: CurrentAuth,
    session: SessionDep,
    search: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """The registry as this bot sees it.

    A builtin command can be overridden per bot (description, aliases, category,
    scope, enabled).  The override is a row in ``bot_commands``; the *handler* is
    never replaced from the webapp, so no endpoint here can run arbitrary code.
    """
    await _owned_bot(session, auth, bot_id)
    rows = (
        await session.scalars(
            select(BotCommandRecord)
            .where(BotCommandRecord.bot_id == bot_id)
            .order_by(BotCommandRecord.category, BotCommandRecord.name)
        )
    ).all()
    custom = [
        CommandResponse.model_validate(row).model_dump() | {"source": "custom"} for row in rows
    ]
    # Commands declared by plugins this bot has installed, even when the plugin
    # is not currently running.  They are listed with ``running=False`` so the
    # Studio never implies a handler is live when it is not.
    from meiko.db.models import Plugin, PluginInstallation
    from meiko.plugins.base import registry as plugin_registry
    from meiko.runtime.manager import get_worker_manager

    installed_rows = (
        await session.execute(
            select(Plugin.slug, PluginInstallation.state, PluginInstallation.id)
            .join(PluginInstallation, PluginInstallation.plugin_id == Plugin.id)
            .where(PluginInstallation.bot_id == bot_id)
        )
    ).all()
    application = get_worker_manager().state.applications.get(bot_id)
    manager = application.plugins if application is not None else None
    declared: list[dict[str, Any]] = []
    for slug, state, installation_id in installed_rows:
        plugin_cls = plugin_registry.get(slug)
        if plugin_cls is None:
            continue
        manifest = plugin_cls.manifest
        running = bool(
            manager is not None
            and manager.is_running(slug, __import__("meiko.plugins.context", fromlist=["PluginScope"]).PluginScope(bot_id=bot_id))
        )
        for command_name in manifest.commands or []:
            clean = str(command_name).lstrip("/").split("@")[0].lower()
            if not clean or command_registry.get(clean) is not None:
                continue
            declared.append(
                {
                    "id": None,
                    "name": clean,
                    "description": f"{manifest.name} plugin command",
                    "category": manifest.category,
                    "aliases": [],
                    "arguments": [],
                    "permission": "everyone",
                    "scope": "all",
                    "enabled": state in ("enabled", "configured", "running"),
                    "owner_only": False,
                    "hidden": False,
                    "handler_type": "plugin",
                    "handler_ref": slug,
                    "definition": {},
                    "usage_count": 0,
                    "plugin": slug,
                    "running": running,
                    "installation_id": installation_id,
                    "source": f"plugin:{slug}",
                }
            )

    custom_names = {row.name for row in rows}
    builtin = []
    for command in command_registry.unique():
        if command.name in custom_names:
            continue  # the custom row already describes it
        builtin.append(
            {
                "id": None,
                "name": command.name,
                "description": command.description,
                "category": command.category,
                "aliases": command.aliases,
                "arguments": [
                    {"name": arg.name, "required": arg.required, "kind": arg.kind,
                     "description": arg.description}
                    for arg in command.arguments
                ],
                "permission": command.permission.name.lower(),
                "scope": command.scope,
                "enabled": True,
                "owner_only": command.permission >= Role.OWNER,
                "hidden": bool(command.hidden),
                "handler_type": "builtin",
                "handler_ref": command.handler.__name__ if command.handler else "",
                "definition": {},
                "usage_count": 0,
                "plugin": command.plugin,
                "source": f"plugin:{command.plugin}" if command.plugin else "core",
            }
        )
    merged = builtin + declared + custom
    if category:
        merged = [row for row in merged if row.get("category") == category]
    if search:
        needle = search.lower().lstrip("/")
        merged = [
            row
            for row in merged
            if needle in row["name"]
            or any(needle in alias for alias in (row.get("aliases") or []))
            or needle in (row.get("description") or "").lower()
        ]
    return merged


@router.get("/categories", response_model=dict)
async def command_categories(bot_id: int, auth: CurrentAuth, session: SessionDep) -> dict[str, int]:
    """Category counts for the Studio's filter chips, computed from the same rows."""
    rows = await list_commands(bot_id, auth, session)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    return counts


@router.post("", response_model=CommandResponse, status_code=201)
async def create_command(
    bot_id: int, payload: CommandRequest, auth: CurrentAuth, session: SessionDep
) -> CommandResponse:
    await _owned_bot(session, auth, bot_id)
    existing = await session.scalar(
        select(BotCommandRecord).where(
            BotCommandRecord.bot_id == bot_id, BotCommandRecord.name == payload.name
        )
    )
    if existing is not None:
        raise error(409, "duplicate_command", "this bot already has a command with that name")
    if command_registry.get(payload.name) is not None and payload.handler_type == "builtin":
        raise error(409, "duplicate_command", "a builtin command already uses that name")
    if payload.handler_type == "declarative":
        definition = dict(payload.definition or {})
        definition.setdefault("trigger", payload.name)
        definition.setdefault("permission", payload.permission)
        definition.setdefault("scope", payload.scope)
        definition.setdefault("description", payload.description)
        try:
            DeclarativeCommand.from_dict(definition)
        except DeclarativeError as exc:
            raise error(422, "invalid_definition", str(exc)) from exc
    record = BotCommandRecord(
        bot_id=bot_id,
        name=payload.name,
        description=payload.description,
        category=payload.category,
        aliases=payload.aliases,
        permission=payload.permission,
        scope=payload.scope,
        enabled=payload.enabled,
        handler_type=payload.handler_type,
        handler_ref=payload.handler_ref,
        definition=payload.definition,
    )
    session.add(record)
    await session.flush()
    await AuditService(session).log(
        "command.created", actor_user_id=auth.user.id, target_type="command", target_id=record.id
    )
    return CommandResponse.model_validate(record)


@router.patch("/{command_id}", response_model=CommandResponse)
async def update_command(
    bot_id: int, command_id: int, payload: CommandRequest, auth: CurrentAuth, session: SessionDep
) -> CommandResponse:
    await _owned_bot(session, auth, bot_id)
    record = await session.scalar(
        select(BotCommandRecord).where(
            BotCommandRecord.id == command_id, BotCommandRecord.bot_id == bot_id
        )
    )
    if record is None:
        raise error(404, "not_found", "command not found")
    if payload.handler_type == "declarative":
        definition = dict(payload.definition or {})
        definition.setdefault("trigger", payload.name)
        try:
            DeclarativeCommand.from_dict(definition)
        except DeclarativeError as exc:
            raise error(422, "invalid_definition", str(exc)) from exc
    record.description = payload.description
    record.category = payload.category
    record.aliases = payload.aliases
    record.permission = payload.permission
    record.scope = payload.scope
    record.enabled = payload.enabled
    record.definition = payload.definition
    await session.flush()
    return CommandResponse.model_validate(record)


@router.delete("/{command_id}", status_code=204)
async def delete_command(
    bot_id: int, command_id: int, auth: CurrentAuth, session: SessionDep
):
    await _owned_bot(session, auth, bot_id)
    record = await session.scalar(
        select(BotCommandRecord).where(
            BotCommandRecord.id == command_id, BotCommandRecord.bot_id == bot_id
        )
    )
    if record is None:
        raise error(404, "not_found", "command not found")
    await session.delete(record)


@router.post("/preview", response_model=dict)
async def preview_command(
    bot_id: int, payload: CommandRequest, auth: CurrentAuth, session: SessionDep
) -> dict:
    """Render a declarative command with sample data – no execution."""
    await _owned_bot(session, auth, bot_id)
    definition = dict(payload.definition or {})
    definition.setdefault("trigger", payload.name)
    try:
        command = DeclarativeCommand.from_dict(definition)
    except DeclarativeError as exc:
        raise error(422, "invalid_definition", str(exc)) from exc
    sample = command.response
    for placeholder, value in {
        "{user}": "@member",
        "{username}": "member",
        "{chat}": "Example Chat",
        "{date}": "2026-01-01",
        "{count}": "0",
        "{bot}": "MEIKO",
    }.items():
        sample = sample.replace(placeholder, value)
    return {"preview": sample[:3600], "buttons": command.buttons, "valid": True}


@router.post("/sync", response_model=dict)
async def sync_to_telegram(bot_id: int, auth: CurrentAuth, session: SessionDep) -> dict:
    bot = await _owned_bot(session, auth, bot_id)
    if not bot.token_valid:
        raise error(400, "token_not_verified", "bot token has not been verified")
    rows = (
        await session.scalars(
            select(BotCommandRecord).where(
                BotCommandRecord.bot_id == bot_id, BotCommandRecord.enabled.is_(True)
            )
        )
    ).all()
    menu = [
        {"command": row.name, "description": row.description[:256]} for row in rows
    ] + command_registry.telegram_menu()
    from meiko.telegram.client import build_bot, set_commands

    client = build_bot(BotService(session).token(bot))
    try:
        await set_commands(client, menu)
    finally:
        await client.session.close()
    return {"synced": len(menu), "commands": menu}


# --------------------------------------------------------------------------- #
# Per-bot overrides of builtin commands
# --------------------------------------------------------------------------- #
from pydantic import BaseModel, Field, model_validator  # noqa: E402


class OverrideRequest(BaseModel):
    """Everything a bot owner may change about a *builtin* command.

    Deliberately absent: ``handler_type``, ``handler_ref``, ``definition``.  A
    builtin command's handler is code in this repository; letting a request name
    a different one would be remote code selection.
    """

    description: str | None = Field(default=None, max_length=256)
    aliases: list[str] | None = None
    category: str | None = Field(default=None, max_length=40)
    scope: str | None = None
    permission: str | None = None
    enabled: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_handler_changes(cls, values: Any) -> Any:
        """Refuse handler fields loudly instead of dropping them silently.

        A request that names ``handler_ref`` is asking for remote code selection.
        The field is not part of this model, so pydantic would ignore it and answer
        200 — a client would believe it had repointed the command. Saying so is the
        only honest response.
        """
        if isinstance(values, dict):
            forbidden = [
                key
                for key in ("handler_type", "handler_ref", "definition", "code")
                if key in values
            ]
            if forbidden:
                raise ValueError(
                    "a command override cannot change its handler ("
                    + ", ".join(forbidden)
                    + "); the console describes and scopes commands, it never selects code"
                )
        return values


@router.get("/{name}/override", response_model=dict)
async def get_override(
    bot_id: int, name: str, auth: CurrentAuth, session: SessionDep
) -> dict:
    await _owned_bot(session, auth, bot_id)
    record = await session.scalar(
        select(BotCommandRecord).where(
            BotCommandRecord.bot_id == bot_id, BotCommandRecord.name == name
        )
    )
    builtin = command_registry.get(name)
    return {
        "name": name,
        "builtin": builtin is not None,
        "plugin": builtin.plugin if builtin else None,
        "override": CommandResponse.model_validate(record).model_dump() if record else None,
        "effective": {
            "description": (record.description if record else None)
            or (builtin.description if builtin else ""),
            "aliases": (record.aliases if record else None) or (builtin.aliases if builtin else []),
            "category": (record.category if record else None)
            or (builtin.category if builtin else "general"),
            "scope": (record.scope if record else None) or (builtin.scope if builtin else "all"),
            "permission": (record.permission if record else None)
            or (builtin.permission.name.lower() if builtin else "everyone"),
            "enabled": record.enabled if record else True,
        },
    }


@router.put("/{name}/override", response_model=dict)
async def set_override(
    bot_id: int, name: str, payload: OverrideRequest, auth: CurrentAuth, session: SessionDep
) -> dict:
    """Create or update the per-bot override for an existing command."""
    await _owned_bot(session, auth, bot_id)
    builtin = command_registry.get(name)
    if builtin is None:
        raise error(404, "not_found", "no such command registered")
    if payload.scope is not None and payload.scope not in ("all", "private", "group"):
        raise error(422, "invalid_scope", "scope must be all, private or group")
    if payload.permission is not None:
        try:
            requested = role_from_string(payload.permission)
        except Exception as exc:  # pragma: no cover - role_from_string is total
            raise error(422, "invalid_permission", str(exc)) from exc
        # A bot owner may tighten or loosen within their own tenant, but a
        # per-bot override can never hand a command to the platform owner level.
        if requested >= Role.OWNER and not auth.principal.has(Role.OWNER):
            raise error(
                403,
                "forbidden",
                "only a platform owner may require the owner role for a command",
            )
    for alias in payload.aliases or []:
        from meiko.core.commands import COMMAND_NAME_RE

        if not COMMAND_NAME_RE.match(alias):
            raise error(422, "invalid_alias", f"invalid alias {alias!r}")
        existing = command_registry.get(alias)
        if existing is not None and existing.name != name:
            raise error(
                409,
                "alias_taken",
                f"/{alias} already belongs to /{existing.name}",
            )
    record = await session.scalar(
        select(BotCommandRecord).where(
            BotCommandRecord.bot_id == bot_id, BotCommandRecord.name == name
        )
    )
    if record is None:
        record = BotCommandRecord(
            bot_id=bot_id,
            name=name,
            description=builtin.description,
            category=builtin.category,
            aliases=list(builtin.aliases),
            permission=builtin.permission.name.lower(),
            scope=builtin.scope,
            enabled=True,
            handler_type="builtin",
            handler_ref=builtin.handler.__name__ if builtin.handler else "",
            definition={},
        )
        session.add(record)
    if payload.description is not None:
        record.description = payload.description
    if payload.aliases is not None:
        record.aliases = list(payload.aliases)
    if payload.category is not None:
        record.category = payload.category
    if payload.scope is not None:
        record.scope = payload.scope
    if payload.permission is not None:
        record.permission = payload.permission
    if payload.enabled is not None:
        record.enabled = payload.enabled
    await session.flush()
    await AuditService(session).log(
        "command.override_set",
        actor_user_id=auth.user.id,
        target_type="command",
        target_id=f"{bot_id}:{name}",
        metadata={"enabled": record.enabled, "scope": record.scope,
                  "permission": record.permission},
    )
    return CommandResponse.model_validate(record).model_dump()


@router.delete("/{name}/override", status_code=204)
async def clear_override(bot_id: int, name: str, auth: CurrentAuth, session: SessionDep):
    """Revert a builtin command to the platform default."""
    await _owned_bot(session, auth, bot_id)
    record = await session.scalar(
        select(BotCommandRecord).where(
            BotCommandRecord.bot_id == bot_id, BotCommandRecord.name == name
        )
    )
    if record is None:
        raise error(404, "not_found", "no override for that command")
    await session.delete(record)
    await AuditService(session).log(
        "command.override_cleared",
        actor_user_id=auth.user.id,
        target_type="command",
        target_id=f"{bot_id}:{name}",
    )


# --------------------------------------------------------------------------- #
# Execution log, testing, import/export
# --------------------------------------------------------------------------- #
@router.get("/logs", response_model=list[dict])
async def command_logs(
    bot_id: int,
    auth: CurrentAuth,
    session: SessionDep,
    limit: int = 100,
    result: str | None = None,
) -> list[dict]:
    """Real invocations recorded by the bot process, newest first."""
    await _owned_bot(session, auth, bot_id)
    stmt = (
        select(CommandExecution)
        .where(CommandExecution.bot_id == bot_id)
        .order_by(CommandExecution.id.desc())
        .limit(min(limit, 500))
    )
    if result:
        stmt = stmt.where(CommandExecution.result == result)
    rows = (await session.scalars(stmt)).all()
    return [
        {
            "id": row.id,
            "command": row.command,
            "chat_type": row.chat_type,
            "chat_id": row.chat_id,
            "telegram_user_id": row.telegram_user_id,
            "result": row.result,
            "error": row.error,
            "duration_ms": row.duration_ms,
            "source": row.source,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/stats", response_model=dict)
async def command_stats(bot_id: int, auth: CurrentAuth, session: SessionDep) -> dict:
    """Usage per command from the execution log - never a synthetic counter."""
    from sqlalchemy import func

    await _owned_bot(session, auth, bot_id)
    rows = (
        await session.execute(
            select(
                CommandExecution.command,
                CommandExecution.result,
                func.count().label("total"),
                func.avg(CommandExecution.duration_ms).label("avg_ms"),
            )
            .where(CommandExecution.bot_id == bot_id)
            .group_by(CommandExecution.command, CommandExecution.result)
        )
    ).all()
    per_command: dict[str, dict[str, Any]] = {}
    for command, result, total, avg_ms in rows:
        entry = per_command.setdefault(
            command, {"command": command, "total": 0, "results": {}, "avg_ms": 0}
        )
        entry["total"] += int(total)
        entry["results"][result] = int(total)
        entry["avg_ms"] = round(float(avg_ms or 0), 1)
    return {
        "commands": sorted(per_command.values(), key=lambda row: row["total"], reverse=True),
        "total": sum(row["total"] for row in per_command.values()),
        "source": "command_executions",
    }


class CommandTestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    text: str = ""
    chat_type: str = "private"
    as_role: str = "everyone"


@router.post("/test", response_model=dict)
async def test_command(
    bot_id: int, payload: CommandTestRequest, auth: CurrentAuth, session: SessionDep
) -> dict:
    """Dry-run a command's *declaration* against a simulated caller.

    This answers the questions the Studio can answer honestly - is it registered,
    does this role pass its permission check, does its scope match this chat type,
    is it enabled for this bot, what does its declarative template render to - and
    it sends nothing to Telegram.  It never executes a handler: running /ban from
    a web form would be a moderation action, not a test.
    """
    await _owned_bot(session, auth, bot_id)
    name = payload.name.lstrip("/").split("@")[0].lower()
    builtin = command_registry.get(name)
    override_row = await session.scalar(
        select(BotCommandRecord).where(
            BotCommandRecord.bot_id == bot_id, BotCommandRecord.name == name
        )
    )
    declared = {
        "command": name,
        "registered": builtin is not None or override_row is not None,
        "plugin": builtin.plugin if builtin else None,
        "category": override_row.category if override_row else (builtin.category if builtin else ""),
        "scope": override_row.scope if override_row else (builtin.scope if builtin else "all"),
        "permission": override_row.permission
        if override_row
        else (builtin.permission.name.lower() if builtin else "everyone"),
        "enabled": override_row.enabled if override_row else True,
        "arguments": [
            {"name": arg.name, "required": arg.required, "kind": arg.kind}
            for arg in (builtin.arguments if builtin else [])
        ],
    }
    checks: list[dict[str, Any]] = []
    required = role_from_string(declared["permission"]) if declared["permission"] else Role.EVERYONE
    acting = role_from_string(payload.as_role)
    checks.append(
        {
            "check": "permission",
            "ok": acting >= required,
            "detail": f"{acting.name.lower()} vs required {required.name.lower()}",
        }
    )
    checks.append(
        {
            "check": "scope",
            "ok": declared["scope"] in ("all", payload.chat_type),
            "detail": f"{declared['scope']} vs chat {payload.chat_type}",
        }
    )
    checks.append(
        {
            "check": "enabled",
            "ok": bool(declared["enabled"]),
            "detail": "enabled for this bot" if declared["enabled"] else "disabled for this bot",
        }
    )
    checks.append(
        {
            "check": "registered",
            "ok": declared["registered"],
            "detail": "handler exists" if builtin else "declaration only (no builtin handler)",
        }
    )
    rendered = None
    if override_row is not None and override_row.definition.get("response"):
        from meiko.core.declarative import (
            DeclarativeCommand,
            DeclarativeError,
            render_values,
        )

        try:
            definition = DeclarativeCommand.from_dict(
                {**override_row.definition, "trigger": name}
            )
            rendered = render_values(
                definition.response,
                {
                    "user": "@tester",
                    "username": "tester",
                    "chat": "Test Chat",
                    "id": "1",
                    "date": "2026-01-01",
                    "count": "0",
                    "bot": "MEIKO",
                },
            )
        except DeclarativeError as exc:
            rendered = f"invalid definition: {exc}"
    would_run = all(check["ok"] for check in checks if check["check"] != "registered")
    session.add(
        CommandExecution(
            bot_id=bot_id,
            command=name[:64],
            chat_type=payload.chat_type,
            result="ok" if would_run else "denied",
            source="studio",
            error="" if would_run else "dry-run refused",
        )
    )
    await session.flush()
    await AuditService(session).log(
        "command.tested",
        actor_user_id=auth.user.id,
        target_type="command",
        target_id=f"{bot_id}:{name}",
        metadata={"would_run": would_run, "as_role": payload.as_role},
    )
    return {
        "command": declared,
        "checks": checks,
        "would_run": would_run,
        "rendered": rendered,
        "sent_to_telegram": False,
        "executed_handler": False,
    }


@router.get("/export", response_model=dict)
async def export_commands(bot_id: int, auth: CurrentAuth, session: SessionDep) -> dict:
    """Everything portable about this bot's command configuration."""
    await _owned_bot(session, auth, bot_id)
    rows = (
        await session.scalars(
            select(BotCommandRecord).where(BotCommandRecord.bot_id == bot_id)
        )
    ).all()
    return {
        "version": 1,
        "bot_id": bot_id,
        "exported_at": utcnow().isoformat(),
        "commands": [
            {
                "name": row.name,
                "description": row.description,
                "category": row.category,
                "aliases": row.aliases,
                "permission": row.permission,
                "scope": row.scope,
                "enabled": row.enabled,
                "handler_type": row.handler_type,
                "handler_ref": row.handler_ref,
                "definition": row.definition,
            }
            for row in rows
        ],
    }


class ImportRequest(BaseModel):
    version: int = 1
    commands: list[CommandRequest]
    replace: bool = False


@router.post("/import", response_model=dict)
async def import_commands(
    bot_id: int, payload: ImportRequest, auth: CurrentAuth, session: SessionDep
) -> dict:
    """Import a configuration export. Declarative definitions are validated."""
    await _owned_bot(session, auth, bot_id)
    if payload.replace:
        existing = (
            await session.scalars(
                select(BotCommandRecord).where(BotCommandRecord.bot_id == bot_id)
            )
        ).all()
        for row in existing:
            await session.delete(row)
        await session.flush()
    created = 0
    updated = 0
    skipped: list[dict[str, str]] = []
    for entry in payload.commands:
        if entry.handler_type == "declarative":
            definition = dict(entry.definition or {})
            definition.setdefault("trigger", entry.name)
            try:
                DeclarativeCommand.from_dict(definition)
            except DeclarativeError as exc:
                skipped.append({"name": entry.name, "reason": str(exc)})
                continue
        record = await session.scalar(
            select(BotCommandRecord).where(
                BotCommandRecord.bot_id == bot_id, BotCommandRecord.name == entry.name
            )
        )
        if record is None:
            record = BotCommandRecord(bot_id=bot_id, name=entry.name)
            session.add(record)
            created += 1
        else:
            updated += 1
        record.description = entry.description
        record.category = entry.category
        record.aliases = entry.aliases
        record.permission = entry.permission
        record.scope = entry.scope
        record.enabled = entry.enabled
        record.handler_type = entry.handler_type
        record.handler_ref = entry.handler_ref
        record.definition = entry.definition
    await session.flush()
    await AuditService(session).log(
        "command.imported",
        actor_user_id=auth.user.id,
        target_type="bot",
        target_id=bot_id,
        metadata={"created": created, "updated": updated, "skipped": len(skipped)},
    )
    return {"created": created, "updated": updated, "skipped": skipped}
