"""Bot management endpoints – every handler is owner scoped."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import func, select

from meiko.api.deps import CurrentAuth, SessionDep, error
from meiko.api.schemas import (
    BotCloneRequest,
    BotCreateRequest,
    BotHealthResponse,
    BotResponse,
    BotUpdateRequest,
    GroupResponse,
    Page,
)
from meiko.db.models import BotAccount, BotGroup, BotTemplate, PluginInstallation
from meiko.runtime.manager import get_worker_manager
from meiko.services.core import (
    AuditService,
    BotService,
    Conflict,
    NotFound,
    ValidationError,
)

router = APIRouter(prefix="/bots", tags=["bots"])


async def verify_bot_token(session, service: BotService, bot, token: str) -> None:
    """Confirm the token with ``getMe``.

    Ownership is proven server-side: the Telegram bot id, username and name all
    come from Telegram, never from the request body.  In the test environment the
    probe is skipped so the suite never depends on the network.
    """
    from meiko.config import get_settings
    from meiko.telegram.client import build_bot, fetch_me

    if get_settings().is_testing:
        bot.last_error = "telegram verification skipped in testing"
        bot.token_valid = False
        return
    client = build_bot(token)
    try:
        me = await fetch_me(client)
        await service.verify_identity(
            bot,
            telegram_bot_id=me["id"],
            username=me.get("username"),
            first_name=me.get("first_name"),
        )
    except Exception as exc:
        bot.last_error = f"Telegram verification failed: {exc}"[:500]
        bot.token_valid = False
    finally:
        await client.session.close()


@router.get("", response_model=Page)
async def list_bots(auth: CurrentAuth, session: SessionDep, limit: int = 50, offset: int = 0) -> Page:
    limit = max(1, min(limit, 200))
    service = BotService(session)
    bots = list(await service.list_for_owner(auth.user.id))
    total = len(bots)
    window = bots[offset : offset + limit]
    return Page(
        items=[BotResponse.model_validate(b).model_dump() for b in window],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
    )


@router.post("", response_model=BotResponse, status_code=status.HTTP_201_CREATED)
async def create_bot(payload: BotCreateRequest, auth: CurrentAuth, session: SessionDep) -> BotResponse:
    service = BotService(session)
    template = None
    if payload.template_slug:
        template = await session.scalar(
            select(BotTemplate).where(BotTemplate.slug == payload.template_slug)
        )
        if template is None:
            raise error(400, "unknown_template", "template not found")
    try:
        bot = await service.register(
            auth.user.id,
            token=payload.token.strip(),
            name=payload.name or "",
            template_id=template.id if template else None,
            settings=dict(template.config or {}) if template else {},
        )
    except (Conflict, ValidationError) as exc:
        raise error(400, "invalid_bot", str(exc)) from exc
    bot.runtime_mode = payload.runtime_mode
    await verify_bot_token(session, service, bot, payload.token.strip())
    if template and template.plugins:
        from meiko.services.core import PluginService

        plugin_service = PluginService(session)
        for slug in template.plugins:
            try:
                await plugin_service.install(slug, bot_id=bot.id, user_id=auth.user.id)
            except (NotFound, ValidationError):
                continue
    await AuditService(session).log(
        "bot.created", actor_user_id=auth.user.id, target_type="bot", target_id=bot.id
    )
    return BotResponse.model_validate(bot)


@router.get("/{bot_id}", response_model=BotResponse)
async def get_bot(bot_id: int, auth: CurrentAuth, session: SessionDep) -> BotResponse:
    try:
        bot = await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    return BotResponse.model_validate(bot)


@router.patch("/{bot_id}", response_model=BotResponse)
async def update_bot(
    bot_id: int, payload: BotUpdateRequest, auth: CurrentAuth, session: SessionDep
) -> BotResponse:
    try:
        bot = await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    if payload.name is not None:
        bot.name = payload.name[:128]
    if payload.description is not None:
        bot.description = payload.description
    if payload.runtime_mode is not None:
        bot.runtime_mode = payload.runtime_mode
    if payload.settings is not None:
        bot.settings = {**(bot.settings or {}), **payload.settings}
    if payload.limits is not None:
        bot.limits = {**(bot.limits or {}), **payload.limits}
    await session.flush()
    await AuditService(session).log(
        "bot.updated", actor_user_id=auth.user.id, target_type="bot", target_id=bot.id
    )
    return BotResponse.model_validate(bot)


@router.delete("/{bot_id}", status_code=204)
async def delete_bot(bot_id: int, auth: CurrentAuth, session: SessionDep):
    try:
        bot = await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    manager = get_worker_manager()
    await manager.stop(bot.id)
    await BotService(session).delete(bot)
    await AuditService(session).log(
        "bot.deleted", actor_user_id=auth.user.id, target_type="bot", target_id=bot_id
    )


@router.post("/{bot_id}/clone", response_model=BotResponse, status_code=201)
async def clone_bot(
    bot_id: int, payload: BotCloneRequest, auth: CurrentAuth, session: SessionDep
) -> BotResponse:
    service = BotService(session)
    try:
        source = await service.get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    try:
        clone = await service.clone_from(
            source,
            auth.user.id,
            token=payload.token.strip(),
            name=payload.name,
            copy_plugins=payload.copy_plugins,
        )
    except (Conflict, ValidationError) as exc:
        raise error(400, "invalid_clone", str(exc)) from exc
    await verify_bot_token(session, service, clone, payload.token.strip())
    await AuditService(session).log(
        "bot.cloned",
        actor_user_id=auth.user.id,
        target_type="bot",
        target_id=clone.id,
        metadata={"source": source.id},
    )
    return BotResponse.model_validate(clone)


@router.post("/{bot_id}/start", response_model=BotHealthResponse)
async def start_bot(bot_id: int, auth: CurrentAuth, session: SessionDep) -> BotHealthResponse:
    try:
        bot = await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    manager = get_worker_manager()
    result = await manager.start(bot.id)
    await AuditService(session).log(
        "bot.started", actor_user_id=auth.user.id, target_type="bot", target_id=bot.id,
        metadata={"result": result.get("status")},
    )
    health = await manager.health(bot.id)
    return BotHealthResponse(
        bot_id=bot.id,
        status=result.get("status", "unknown"),
        mode=bot.runtime_mode,
        running=result.get("status") == "running",
        uptime_seconds=health.get("uptime_seconds", 0),
        plugin_count=health.get("plugin_count", 0),
        last_error=result.get("error"),
        worker=health.get("worker"),
        available=result.get("status") != "error",
        note=result.get("note"),
    )


@router.post("/{bot_id}/stop", response_model=BotHealthResponse)
async def stop_bot(bot_id: int, auth: CurrentAuth, session: SessionDep) -> BotHealthResponse:
    try:
        bot = await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    manager = get_worker_manager()
    await manager.stop(bot.id)
    await AuditService(session).log(
        "bot.stopped", actor_user_id=auth.user.id, target_type="bot", target_id=bot.id
    )
    return BotHealthResponse(
        bot_id=bot.id, status="stopped", mode=bot.runtime_mode, running=False
    )


@router.post("/{bot_id}/restart", response_model=BotHealthResponse)
async def restart_bot(bot_id: int, auth: CurrentAuth, session: SessionDep) -> BotHealthResponse:
    try:
        bot = await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    manager = get_worker_manager()
    await manager.stop(bot.id)
    result = await manager.start(bot.id)
    await AuditService(session).log(
        "bot.restarted", actor_user_id=auth.user.id, target_type="bot", target_id=bot.id
    )
    health = await manager.health(bot.id)
    return BotHealthResponse(
        bot_id=bot.id,
        status=result.get("status", "unknown"),
        mode=bot.runtime_mode,
        running=result.get("status") == "running",
        uptime_seconds=health.get("uptime_seconds", 0),
        plugin_count=health.get("plugin_count", 0),
        last_error=result.get("error"),
        worker=health.get("worker"),
    )


@router.get("/{bot_id}/health", response_model=BotHealthResponse)
async def bot_health(bot_id: int, auth: CurrentAuth, session: SessionDep) -> BotHealthResponse:
    try:
        bot = await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    manager = get_worker_manager()
    health = await manager.health(bot.id)
    return BotHealthResponse(
        bot_id=bot.id,
        status=health.get("status", bot.status),
        mode=bot.runtime_mode,
        running=health.get("status") == "running",
        uptime_seconds=health.get("uptime_seconds", 0),
        plugin_count=health.get("plugin_count", 0),
        last_error=bot.last_error,
        worker=health.get("worker"),
        available=True,
    )


@router.get("/{bot_id}/groups", response_model=list[GroupResponse])
async def bot_groups(bot_id: int, auth: CurrentAuth, session: SessionDep) -> list[GroupResponse]:
    try:
        await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    rows = (
        await session.scalars(select(BotGroup).where(BotGroup.bot_id == bot_id).order_by(BotGroup.title))
    ).all()
    return [GroupResponse.model_validate(row) for row in rows]


@router.post("/{bot_id}/commands/sync")
async def sync_commands(bot_id: int, auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    """Push the enabled command menu to Telegram for this bot."""
    try:
        bot = await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    if not bot.token_valid:
        raise error(400, "token_not_verified", "bot token has not been verified with Telegram")
    from meiko.core.commands import registry
    from meiko.telegram.client import build_bot, set_commands

    token = BotService(session).token(bot)
    client = build_bot(token)
    try:
        commands = registry.telegram_menu()
        await set_commands(client, commands)
    finally:
        await client.session.close()
    await AuditService(session).log(
        "bot.commands_synced", actor_user_id=auth.user.id, target_type="bot", target_id=bot.id
    )
    return {"synced": len(commands), "commands": commands}
