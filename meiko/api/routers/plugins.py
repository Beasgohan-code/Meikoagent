"""Plugin marketplace endpoints: catalogue, install, configure, enable, logs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import select

from meiko.api.deps import CurrentAuth, SessionDep, error
from meiko.api.schemas import (
    PluginConfigureRequest,
    PluginInstallationResponse,
    PluginInstallRequest,
    PluginResponse,
)
from meiko.db.models import Plugin, PluginInstallation, PluginLog
from meiko.plugins.base import registry as plugin_registry
from meiko.plugins.context import PluginScope
from meiko.plugins.manager import PluginManager
from meiko.runtime.manager import get_worker_manager
from meiko.services.core import AuditService, BotService, NotFound, PluginService, ValidationError

router = APIRouter(prefix="/plugins", tags=["plugins"])


async def _sync_runtime(bot_id: int | None) -> dict[str, Any]:
    """Apply a plugin state change to a running bot immediately."""
    if bot_id is None:
        return {"applied": False, "reason": "global scope is materialised per bot"}
    manager = get_worker_manager()
    application = manager.state.applications.get(bot_id)
    if application is None:
        return {"applied": False, "reason": "bot is not running; state applies on next start"}
    return {"applied": True, "states": await application.load_plugins()}


@router.get("", response_model=list[PluginResponse])
async def catalogue(auth: CurrentAuth, session: SessionDep, category: str | None = None) -> list[PluginResponse]:
    stmt = select(Plugin).order_by(Plugin.category, Plugin.name)
    if category:
        stmt = stmt.where(Plugin.category == category)
    rows = (await session.scalars(stmt)).all()
    return [PluginResponse.model_validate(row) for row in rows]


@router.get("/categories", response_model=dict[str, int])
async def categories(auth: CurrentAuth, session: SessionDep) -> dict[str, int]:
    rows = (await session.scalars(select(Plugin))).all()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.category] = counts.get(row.category, 0) + 1
    return counts


@router.get("/installed", response_model=list[dict])
async def installed(
    auth: CurrentAuth, session: SessionDep, bot_id: int | None = None
) -> list[dict]:
    if bot_id is not None:
        try:
            await BotService(session).get(bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "bot not found") from exc
    service = PluginService(session)
    if bot_id is None:
        bots = await BotService(session).list_for_owner(auth.user.id)
        bot_ids = [b.id for b in bots]
        rows = (
            await session.scalars(
                select(PluginInstallation).where(
                    PluginInstallation.bot_id.in_(bot_ids) if bot_ids else False
                )
            )
        ).all()
    else:
        rows = await service.installations_for_bot(bot_id)
    out = []
    for row in rows:
        plugin = await session.get(Plugin, row.plugin_id)
        payload = PluginInstallationResponse.model_validate(row).model_dump()
        payload["plugin"] = (
            PluginResponse.model_validate(plugin).model_dump() if plugin else None
        )
        out.append(payload)
    return out


@router.get("/{slug}", response_model=dict)
async def plugin_detail(slug: str, auth: CurrentAuth, session: SessionDep) -> dict:
    plugin = await session.scalar(select(Plugin).where(Plugin.slug == slug))
    if plugin is None:
        raise error(404, "not_found", "plugin not found")
    implemented = slug in plugin_registry.slugs()
    return {
        **PluginResponse.model_validate(plugin).model_dump(),
        "implemented": implemented,
        "manifest": plugin.manifest,
        "installable": implemented and plugin.status == "approved",
        "note": None
        if implemented
        else "No approved implementation is installed for this plugin, so it cannot be enabled.",
    }


@router.post("/{slug}/install", response_model=PluginInstallationResponse, status_code=201)
async def install(
    slug: str, payload: PluginInstallRequest, auth: CurrentAuth, session: SessionDep
) -> PluginInstallationResponse:
    if payload.bot_id is not None:
        try:
            await BotService(session).get(payload.bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "bot not found") from exc
    try:
        record = await PluginService(session).install(
            slug, bot_id=payload.bot_id, group_id=payload.group_id, user_id=auth.user.id
        )
    except (NotFound, ValidationError) as exc:
        raise error(400, "install_failed", str(exc)) from exc
    await AuditService(session).log(
        "plugin.installed", actor_user_id=auth.user.id, target_type="plugin", target_id=slug,
        metadata={"bot_id": payload.bot_id},
    )
    return PluginInstallationResponse.model_validate(record)


@router.patch("/installations/{installation_id}/configure", response_model=PluginInstallationResponse)
async def configure(
    installation_id: int, payload: PluginConfigureRequest, auth: CurrentAuth, session: SessionDep
) -> PluginInstallationResponse:
    record = await session.get(PluginInstallation, installation_id)
    if record is None:
        raise error(404, "not_found", "installation not found")
    if record.bot_id is not None:
        try:
            await BotService(session).get(record.bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "installation not found") from exc
    try:
        record = await PluginService(session).configure(record, payload.config)
    except ValidationError as exc:
        raise error(422, "invalid_config", str(exc)) from exc
    await AuditService(session).log(
        "plugin.configured", actor_user_id=auth.user.id, target_type="installation",
        target_id=installation_id,
    )
    await _sync_runtime(record.bot_id)
    return PluginInstallationResponse.model_validate(record)


@router.post("/installations/{installation_id}/enable", response_model=dict)
async def enable(installation_id: int, auth: CurrentAuth, session: SessionDep) -> dict:
    record = await session.get(PluginInstallation, installation_id)
    if record is None:
        raise error(404, "not_found", "installation not found")
    if record.bot_id is not None:
        try:
            await BotService(session).get(record.bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "installation not found") from exc
    service = PluginService(session)
    try:
        await service.enable(record)
    except ValidationError as exc:
        raise error(422, "config_required", str(exc)) from exc
    runtime = await _sync_runtime(record.bot_id)
    plugin = await session.get(Plugin, record.plugin_id)
    await AuditService(session).log(
        "plugin.enabled", actor_user_id=auth.user.id, target_type="installation",
        target_id=installation_id,
    )
    return {
        "state": record.state,
        "runtime": runtime,
        "plugin": plugin.slug if plugin else None,
        "commands": [],
    }


@router.post("/installations/{installation_id}/disable", response_model=dict)
async def disable(installation_id: int, auth: CurrentAuth, session: SessionDep) -> dict:
    record = await session.get(PluginInstallation, installation_id)
    if record is None:
        raise error(404, "not_found", "installation not found")
    if record.bot_id is not None:
        try:
            await BotService(session).get(record.bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "installation not found") from exc
    await PluginService(session).disable(record)
    if record.bot_id is not None:
        manager = get_worker_manager()
        application = manager.state.applications.get(record.bot_id)
        plugin = await session.get(Plugin, record.plugin_id)
        if application is not None and application.plugins is not None and plugin is not None:
            scope = PluginScope(bot_id=record.bot_id, group_id=record.group_id)
            await application.plugins.stop(plugin.slug, scope, installation=record)
    await AuditService(session).log(
        "plugin.disabled", actor_user_id=auth.user.id, target_type="installation",
        target_id=installation_id,
    )
    return {"state": record.state}


@router.post("/installations/{installation_id}/restart", response_model=dict)
async def restart(installation_id: int, auth: CurrentAuth, session: SessionDep) -> dict:
    record = await session.get(PluginInstallation, installation_id)
    if record is None:
        raise error(404, "not_found", "installation not found")
    if record.bot_id is not None:
        try:
            await BotService(session).get(record.bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "installation not found") from exc
    runtime = await _sync_runtime(record.bot_id)
    await AuditService(session).log(
        "plugin.restarted", actor_user_id=auth.user.id, target_type="installation",
        target_id=installation_id,
    )
    return {"runtime": runtime, "state": record.state}


@router.delete("/installations/{installation_id}", status_code=204)
async def uninstall(installation_id: int, auth: CurrentAuth, session: SessionDep):
    record = await session.get(PluginInstallation, installation_id)
    if record is None:
        raise error(404, "not_found", "installation not found")
    if record.bot_id is not None:
        try:
            await BotService(session).get(record.bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "installation not found") from exc
    if record.bot_id is not None:
        manager = get_worker_manager()
        application = manager.state.applications.get(record.bot_id)
        plugin = await session.get(Plugin, record.plugin_id)
        if application is not None and application.plugins is not None and plugin is not None:
            await application.plugins.stop(
                plugin.slug,
                PluginScope(bot_id=record.bot_id, group_id=record.group_id),
                installation=record,
                new_state="available",
            )
    await PluginService(session).uninstall(record)
    await AuditService(session).log(
        "plugin.uninstalled", actor_user_id=auth.user.id, target_type="installation",
        target_id=installation_id,
    )


@router.get("/installations/{installation_id}/logs", response_model=list[dict])
async def logs(
    installation_id: int, auth: CurrentAuth, session: SessionDep, limit: int = 100
) -> list[dict]:
    record = await session.get(PluginInstallation, installation_id)
    if record is None:
        raise error(404, "not_found", "installation not found")
    if record.bot_id is not None:
        try:
            await BotService(session).get(record.bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "installation not found") from exc
    rows = (
        await session.scalars(
            select(PluginLog)
            .where(PluginLog.installation_id == installation_id)
            .order_by(PluginLog.created_at.desc())
            .limit(min(limit, 500))
        )
    ).all()
    return [
        {"id": row.id, "level": row.level, "message": row.message, "created_at": row.created_at.isoformat()}
        for row in rows
    ]
