"""Command Studio: registry + safe declarative commands + Telegram sync."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import select

from meiko.api.deps import CurrentAuth, SessionDep, error
from meiko.api.schemas import CommandRequest, CommandResponse
from meiko.core.commands import registry as command_registry
from meiko.core.declarative import DeclarativeCommand, DeclarativeError
from meiko.db.models import BotCommandRecord
from meiko.services.core import AuditService, BotService, NotFound

router = APIRouter(prefix="/bots/{bot_id}/commands", tags=["commands"])


async def _owned_bot(session, auth, bot_id: int):
    try:
        return await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc


@router.get("", response_model=list[dict])
async def list_commands(bot_id: int, auth: CurrentAuth, session: SessionDep) -> list[dict]:
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
    builtin = [
        {
            "id": None,
            "name": command.name,
            "description": command.description,
            "category": command.category,
            "aliases": command.aliases,
            "permission": command.permission.name.lower(),
            "scope": command.scope,
            "enabled": True,
            "handler_type": "builtin",
            "handler_ref": command.handler.__name__ if command.handler else "",
            "definition": {},
            "usage_count": 0,
            "source": f"plugin:{command.plugin}" if command.plugin else "core",
        }
        for command in command_registry.unique()
    ]
    return builtin + custom


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
