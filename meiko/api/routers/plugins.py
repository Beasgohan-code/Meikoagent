"""Plugin marketplace endpoints: catalogue, install, configure, enable, logs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
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
from meiko.runtime.manager import get_worker_manager
from meiko.services.core import AuditService, BotService, NotFound, PluginService, ValidationError

router = APIRouter(prefix="/plugins", tags=["plugins"])

ACTIVE_STATES = ("enabled", "configured", "running")


async def _sync_runtime(bot_id: int | None) -> dict[str, Any]:
    """Apply a plugin state change to a running bot immediately."""
    if bot_id is None:
        return {"applied": False, "reason": "global scope is materialised per bot"}
    manager = get_worker_manager()
    application = manager.state.applications.get(bot_id)
    if application is None:
        return {"applied": False, "reason": "bot is not running; state applies on next start"}
    return {"applied": True, "states": await application.load_plugins()}


def _missing_config(plugin: Plugin, record: PluginInstallation | None) -> list[str]:
    """Required config keys still empty for this installation."""
    schema = (plugin.config_schema or {}) if plugin else {}
    values = (record.config or {}) if record else {}
    missing = []
    for key, spec in schema.items():
        if not isinstance(spec, dict) or not spec.get("required"):
            continue
        current = values.get(key, spec.get("default"))
        if current in (None, "", [], {}):
            missing.append(key)
    return missing


async def _stop_runtime(record: PluginInstallation, slug: str) -> dict[str, Any]:
    if record.bot_id is None:
        return {"applied": False, "reason": "global scope is materialised per bot"}
    manager = get_worker_manager()
    application = manager.state.applications.get(record.bot_id)
    if application is None or application.plugins is None:
        return {"applied": False, "reason": "bot is not running"}
    scope = PluginScope(bot_id=record.bot_id, group_id=record.group_id)
    await application.plugins.stop(slug, scope, installation=record)
    return {"applied": True, "stopped": slug}


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


@router.get("/toggles", response_model=list[dict])
async def toggles(
    auth: CurrentAuth, session: SessionDep, bot_id: int | None = None
) -> list[dict]:
    """Every implemented plugin with its on/off state for one scope.

    This backs the switch grid in the webapp: one row per builtin plugin, with
    the installation id (when installed) so the UI can flip it in one click.
    """
    if bot_id is not None:
        try:
            await BotService(session).get(bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "bot not found") from exc
        installations = await PluginService(session).installations_for_bot(bot_id)
    else:
        bots = await BotService(session).list_for_owner(auth.user.id)
        bot_ids = [bot.id for bot in bots]
        installations = (
            await session.scalars(
                select(PluginInstallation).where(
                    PluginInstallation.bot_id.in_(bot_ids) if bot_ids else False
                )
            )
        ).all()
    by_plugin: dict[int, PluginInstallation] = {row.plugin_id: row for row in installations}
    catalogue = (await session.scalars(select(Plugin).order_by(Plugin.category, Plugin.name))).all()
    implemented = set(plugin_registry.slugs())
    out: list[dict] = []
    for plugin in catalogue:
        if plugin.slug not in implemented:
            continue
        record = by_plugin.get(plugin.id)
        state = record.state if record else "available"
        missing = _missing_config(plugin, record)
        out.append(
            {
                "slug": plugin.slug,
                "name": plugin.name,
                "icon": plugin.icon,
                "category": plugin.category,
                "description": plugin.description,
                "state": state,
                "enabled": state in ACTIVE_STATES,
                "installation_id": record.id if record else None,
                "scope": record.scope if record else None,
                "missing_config": missing,
            }
        )
    return out


async def _effective_states(session: Any, auth: Any) -> dict[int, dict[str, dict[str, Any]]]:
    """Per-bot truth: is this module actually active for that bot?

    A module can be installed globally *and* overridden per bot, and a bot can be
    stopped.  The dashboards need one answer per (bot, slug), so this resolves the
    precedence (bot > group > global) instead of leaving the UI to guess.
    """
    from meiko.runtime.manager import get_worker_manager

    bots = await BotService(session).list_for_owner(auth.user.id)
    bot_ids = [bot.id for bot in bots]
    if not bot_ids:
        return {}
    globals_by_plugin = {
        row.plugin_id: row
        for row in (
            await session.scalars(
                select(PluginInstallation).where(PluginInstallation.bot_id.is_(None))
            )
        ).all()
    }
    rows = (
        await session.scalars(
            select(PluginInstallation).where(PluginInstallation.bot_id.in_(bot_ids))
        )
    ).all()
    catalogue = {plugin.id: plugin.slug for plugin in (await session.scalars(select(Plugin))).all()}
    manager = get_worker_manager()
    out: dict[int, dict[str, dict[str, Any]]] = {}
    for bot in bots:
        running = bot.id in manager.state.applications
        per_bot: dict[str, dict[str, Any]] = {}
        for plugin_id, row in globals_by_plugin.items():
            slug = catalogue.get(plugin_id)
            if slug is None:
                continue
            per_bot[slug] = {
                "state": row.state,
                "enabled": row.state in ACTIVE_STATES,
                "scope": "global",
                "installation_id": row.id,
                "running": running and row.state in ACTIVE_STATES,
            }
        for row in rows:
            if row.bot_id != bot.id:
                continue
            slug = catalogue.get(row.plugin_id)
            if slug is None:
                continue
            per_bot[slug] = {
                "state": row.state,
                "enabled": row.state in ACTIVE_STATES,
                "scope": row.scope,
                "installation_id": row.id,
                "running": running and row.state in ACTIVE_STATES,
            }
        out[bot.id] = per_bot
    return out


@router.get("/effective", response_model=dict)
async def effective_plugins(auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    """Which modules are enabled for which bot, and whether the bot is running.

    This is what gates every module dashboard: a disabled module must not look
    active in the UI either.
    """
    states = await _effective_states(session, auth)
    from meiko.runtime.manager import get_worker_manager

    applications = get_worker_manager().state.applications
    return {
        "bots": {
            str(bot_id): {
                "running": bot_id in applications,
                "plugins": plugins,
            }
            for bot_id, plugins in states.items()
        }
    }


@router.post("/{slug}/toggle", response_model=dict)
async def toggle_by_slug(
    slug: str,
    auth: CurrentAuth,
    session: SessionDep,
    bot_id: int | None = None,
    enabled: bool = True,
) -> dict:
    """Install-if-needed then enable/disable, in a single call.

    The UI switch should not make the user walk three screens: flipping it on an
    uninstalled plugin installs it (into ``bot_id`` or globally) and enables it.
    """
    plugin = await session.scalar(select(Plugin).where(Plugin.slug == slug))
    if plugin is None:
        raise error(404, "not_found", "plugin not found")
    if slug not in set(plugin_registry.slugs()):
        raise error(422, "not_implemented", "no runtime implementation is registered")
    if bot_id is not None:
        try:
            await BotService(session).get(bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "bot not found") from exc
    service = PluginService(session)
    record = await service.installation(slug, bot_id)
    if record is None:
        try:
            record = await service.install(slug, bot_id=bot_id, user_id=auth.user.id)
        except (NotFound, ValidationError) as exc:
            raise error(400, "install_failed", str(exc)) from exc
    runtime: dict[str, Any]
    if enabled:
        missing = _missing_config(plugin, record)
        if missing:
            raise error(
                422,
                "config_required",
                "configure before enabling: " + ", ".join(missing),
            )
        try:
            await service.enable(record)
        except ValidationError as exc:
            raise error(422, "config_required", str(exc)) from exc
        runtime = await _sync_runtime(record.bot_id)
    else:
        await service.disable(record)
        runtime = await _stop_runtime(record, plugin.slug)
    await AuditService(session).log(
        "plugin.toggled",
        actor_user_id=auth.user.id,
        target_type="plugin",
        target_id=slug,
        metadata={"enabled": enabled, "bot_id": bot_id},
    )
    return {
        "slug": slug,
        "state": record.state,
        "enabled": record.state in ACTIVE_STATES,
        "installation_id": record.id,
        "runtime": runtime,
    }


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
