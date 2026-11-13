"""Plugin marketplace endpoints: catalogue, install, configure, enable, logs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from meiko.api.deps import CurrentAuth, SessionDep, error
from meiko.api.plugin_authz import (
    authorize_installation,
    is_platform_owner,
    owner_metadata,
    require_scope,
)
from meiko.api.schemas import (
    PluginConfigureRequest,
    PluginInstallationResponse,
    PluginInstallRequest,
    PluginResponse,
)
from meiko.db.models import BotGroup, Plugin, PluginInstallation, PluginLog
from meiko.plugins.base import registry as plugin_registry
from meiko.plugins.manager import (
    RUNTIME_STATUSES,
    missing_permissions,
    permission_changes,
    runtime_status,
)
from meiko.plugins.context import PluginScope
from meiko.runtime.manager import get_worker_manager
from meiko.services.core import AuditService, BotService, NotFound, PluginService, ValidationError

router = APIRouter(prefix="/plugins", tags=["plugins"])

ACTIVE_STATES = ("enabled", "configured", "running")

#: How "active" each installation state is – used only to pick which row best
#: describes a plugin that is installed in several places.
_STATE_RANK = {
    "available": 0,
    "disabled": 1,
    "installed": 2,
    "enabled": 3,
    "configured": 4,
    "running": 5,
}


async def _sync_runtime(bot_id: int | None, slug: str | None = None) -> dict[str, Any]:
    """Apply a plugin state change to the running bot(s) immediately.

    Two cases, both honest:

    * a bot-scoped or group-scoped row -> reconcile *that* bot now;
    * a global row -> reconcile **every** running bot, because the change is
      platform-wide.  The old answer ("global scope is materialised per bot")
      meant a global enable did nothing until each bot restarted, which is not
      reconciliation, it is a TODO.
    """
    manager = get_worker_manager()
    if bot_id is None:
        if not manager.state.applications:
            return {
                "applied": False,
                "reason": "no bots are running; global state applies when they start",
            }
        states: dict[str, dict[str, str]] = {}
        for running_id, application in list(manager.state.applications.items()):
            if application.plugins is None:
                continue
            states[str(running_id)] = await application.load_plugins()
        if not states:
            return {"applied": False, "reason": "no running bot has plugins loaded"}
        return {"applied": True, "scope": "global", "bots": states}
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
    """Stop a plugin instance in the scope(s) the installation covers."""
    manager = get_worker_manager()
    if record.bot_id is None and record.group_id is None:
        stopped: list[str] = []
        for running_id, application in list(manager.state.applications.items()):
            if application.plugins is None:
                continue
            await application.plugins.stop(
                slug, PluginScope(bot_id=running_id), installation=None
            )
            stopped.append(str(running_id))
        if not stopped:
            return {"applied": False, "reason": "no bots are running"}
        return {"applied": True, "scope": "global", "stopped": stopped}
    bot_id = record.bot_id
    if bot_id is None:
        return {"applied": False, "reason": "group scope needs the bot that owns the group"}
    application = manager.state.applications.get(bot_id)
    if application is None or application.plugins is None:
        return {"applied": False, "reason": "bot is not running"}
    scope = PluginScope(bot_id=bot_id, group_id=record.group_id)
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
        stmt = select(PluginInstallation).where(
            PluginInstallation.bot_id.in_(bot_ids) if bot_ids else False
        )
        if is_platform_owner(auth):
            # The owner also sees global rows, because they are the only caller
            # allowed to manage them.
            stmt = select(PluginInstallation).where(
                (PluginInstallation.bot_id.in_(bot_ids))
                | (PluginInstallation.bot_id.is_(None))
            )
        rows = list((await session.scalars(stmt)).all())
    else:
        rows = list(await service.installations_for_bot(bot_id))
        group_ids = [r.group_id for r in rows if r.group_id is not None]
        if group_ids:
            groups = (
                await session.scalars(select(BotGroup).where(BotGroup.id.in_(group_ids)))
            ).all()
            stale = [g.id for g in groups if g.bot_id != bot_id]
            if stale:
                rows = [r for r in rows if r.group_id not in stale]
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
        installations = list(await PluginService(session).installations_for_bot(bot_id))
    else:
        bots = await BotService(session).list_for_owner(auth.user.id)
        bot_ids = [bot.id for bot in bots]
        stmt = select(PluginInstallation).where(
            PluginInstallation.bot_id.in_(bot_ids) if bot_ids else False
        )
        if is_platform_owner(auth):
            stmt = select(PluginInstallation).where(
                (PluginInstallation.bot_id.in_(bot_ids))
                | (PluginInstallation.bot_id.is_(None))
            )
        installations = list((await session.scalars(stmt)).all())
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

    Three things used to be wrong here and each one made the dashboards lie:

    1. ``running`` was inferred from the bot process being up
       (``bot_id in applications``), not from the plugin runtime – a bot that was
       running with the plugin disabled still showed "running".
    2. group-scope rows were ignored completely, so a module enabled for one
       group did not exist as far as the UI was concerned.
    3. global rows were sprinkled over every bot without asking the runtime
       whether they had actually been materialised.

    Every answer below comes from the runtime manager, and precedence is resolved
    with the same ``PluginManager.resolve_scope`` the bot itself uses, so the API
    and the runtime cannot disagree.
    """
    from meiko.plugins.manager import PluginManager
    from meiko.runtime.manager import get_worker_manager

    bots = await BotService(session).list_for_owner(auth.user.id)
    bot_ids = [bot.id for bot in bots]
    if not bot_ids:
        return {}
    groups_by_id = {
        group.id: group
        for group in (
            await session.scalars(select(BotGroup).where(BotGroup.bot_id.in_(bot_ids)))
        ).all()
    }
    global_rows = (
        await session.scalars(
            select(PluginInstallation).where(PluginInstallation.bot_id.is_(None))
        )
    ).all()
    bot_rows = (
        await session.scalars(
            select(PluginInstallation).where(PluginInstallation.bot_id.in_(bot_ids))
        )
    ).all()
    group_rows = (
        list(
            (
                await session.scalars(
                    select(PluginInstallation).where(
                        PluginInstallation.group_id.in_(list(groups_by_id))
                    )
                )
            ).all()
        )
        if groups_by_id
        else []
    )
    catalogue = {
        plugin.id: plugin for plugin in (await session.scalars(select(Plugin))).all()
    }
    implemented = set(plugin_registry.slugs())
    manager = get_worker_manager()
    out: dict[int, dict[str, dict[str, Any]]] = {}
    for bot in bots:
        application = manager.state.applications.get(bot.id)
        plugin_manager = application.plugins if application is not None else None
        per_bot: dict[str, dict[str, Any]] = {}
        for slug in sorted(implemented):
            plugin = next((p for p in catalogue.values() if p.slug == slug), None)
            if plugin is None:
                continue
            group_installs = [
                row
                for row in group_rows
                if row.plugin_id == plugin.id
                and groups_by_id.get(row.group_id) is not None
                and groups_by_id[row.group_id].bot_id == bot.id
            ]
            candidates = [
                row for row in bot_rows if row.bot_id == bot.id and row.plugin_id == plugin.id
            ] + group_installs + [row for row in global_rows if row.plugin_id == plugin.id]
            resolved = PluginManager.resolve_scope(candidates, slug, bot.id, None)
            if resolved is None and group_installs:
                # Nothing at bot/global level, but the plugin is active inside
                # specific groups: report the group row rather than pretending
                # the module is not installed at all.
                resolved = max(group_installs, key=lambda row: _STATE_RANK.get(row.state, 0))
            group_overrides = [
                {
                    "group_id": row.group_id,
                    "group_title": groups_by_id[row.group_id].title,
                    "state": row.state,
                    "installation_id": row.id,
                    "missing_config": _missing_config(plugin, row),
                }
                for row in group_installs
            ]
            running_in_groups = []
            if plugin_manager is not None:
                running_in_groups = [
                    entry["group_id"]
                    for entry in plugin_manager.running_summary()
                    if entry["slug"] == slug
                    and entry["bot_id"] == bot.id
                    and entry["group_id"] is not None
                ]
            live = bool(running_in_groups) or (
                plugin_manager.is_running(slug, PluginScope(bot_id=bot.id))
                if plugin_manager is not None
                else False
            )
            status = runtime_status(
                plugin=plugin,
                record=resolved,
                live=live,
                bot_running=application is not None,
            )
            per_bot[slug] = {
                "state": resolved.state if resolved else "available",
                "scope": resolved.scope if resolved else None,
                "installation_id": resolved.id if resolved else None,
                "enabled": bool(resolved and resolved.state in ACTIVE_STATES),
                "running": live,
                "status": status,
                "implemented": slug in implemented,
                "missing_config": _missing_config(plugin, resolved),
                "missing_permissions": missing_permissions(plugin),
                "group_overrides": group_overrides,
                "running_in_groups": running_in_groups,
            }
        out[bot.id] = per_bot
    return out


@router.get("/effective", response_model=dict)
async def effective_plugins(auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    """Which modules are actually active for which bot, read from the runtime.

    ``running`` is the plugin manager's answer, not "the bot process is alive".
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
        },
        "statuses": list(RUNTIME_STATUSES),
    }


@router.post("/{slug}/toggle", response_model=dict)
async def toggle_by_slug(
    slug: str,
    auth: CurrentAuth,
    session: SessionDep,
    bot_id: int | None = None,
    enabled: bool = True,
    group_id: int | None = None,
) -> dict:
    """Install-if-needed then enable/disable, in a single call.

    Flipping a switch on an uninstalled plugin installs it into the scope the
    caller names – and that scope is authorized *before* anything is written:
    ``bot_id`` must be a bot the caller owns, and a global toggle (no ``bot_id``)
    is a platform-owner action.
    """
    plugin = await session.scalar(select(Plugin).where(Plugin.slug == slug))
    if plugin is None:
        raise error(404, "not_found", "plugin not found")
    if slug not in set(plugin_registry.slugs()):
        raise error(422, "not_implemented", "no runtime implementation is registered")
    grant = await require_scope(auth, session, bot_id=bot_id, group_id=group_id)
    service = PluginService(session)
    record = await service.installation(slug, bot_id, group_id)
    created = False
    if record is None:
        try:
            record = await service.install(
                slug, bot_id=bot_id, group_id=group_id, user_id=auth.user.id
            )
        except (NotFound, ValidationError) as exc:
            raise error(400, "install_failed", str(exc)) from exc
        created = True
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
        runtime = await _sync_runtime(record.bot_id, plugin.slug)
    else:
        await service.disable(record)
        runtime = await _stop_runtime(record, plugin.slug)
    await AuditService(session).log(
        "plugin.toggled",
        actor_user_id=auth.user.id,
        target_type="plugin",
        target_id=slug,
        metadata=owner_metadata(
            auth, enabled=enabled, bot_id=bot_id, group_id=group_id,
            scope=grant.scope, created=created,
        ),
    )
    return {
        "slug": slug,
        "state": record.state,
        "enabled": record.state in ACTIVE_STATES,
        "installation_id": record.id,
        "scope": grant.scope,
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
        "missing_permissions": missing_permissions(plugin),
        "pending_permission_changes": permission_changes(plugin),
        "note": None
        if implemented
        else "No approved implementation is installed for this plugin, so it cannot be enabled.",
    }


@router.post("/{slug}/install", response_model=PluginInstallationResponse, status_code=201)
async def install(
    slug: str, payload: PluginInstallRequest, auth: CurrentAuth, session: SessionDep
) -> PluginInstallationResponse:
    """Install a plugin into a scope the caller is allowed to own.

    The ``bot_id`` is not optional for ordinary users: without one the request
    means "global scope", which is a platform-owner capability.  That is the fix
    for the escalated install the earlier API allowed.
    """
    grant = await require_scope(
        auth, session, bot_id=payload.bot_id, group_id=payload.group_id
    )
    try:
        record = await PluginService(session).install(
            slug,
            bot_id=payload.bot_id,
            group_id=payload.group_id,
            user_id=auth.user.id,
        )
    except (NotFound, ValidationError) as exc:
        raise error(400, "install_failed", str(exc)) from exc
    action = "plugin.global_installed" if grant.is_global else "plugin.installed"
    await AuditService(session).log(
        action,
        actor_user_id=auth.user.id,
        target_type="plugin",
        target_id=slug,
        metadata=owner_metadata(
            auth,
            bot_id=payload.bot_id,
            group_id=payload.group_id,
            scope=grant.scope,
        ),
    )
    return PluginInstallationResponse.model_validate(record)


@router.patch("/installations/{installation_id}/configure", response_model=PluginInstallationResponse)
async def configure(
    installation_id: int, payload: PluginConfigureRequest, auth: CurrentAuth, session: SessionDep
) -> PluginInstallationResponse:
    record = await authorize_installation(
        auth, session, await session.get(PluginInstallation, installation_id)
    )
    try:
        record = await PluginService(session).configure(record, payload.config)
    except ValidationError as exc:
        raise error(422, "invalid_config", str(exc)) from exc
    await AuditService(session).log(
        "plugin.configured",
        actor_user_id=auth.user.id,
        target_type="installation",
        target_id=installation_id,
        metadata=owner_metadata(auth, scope=record.scope),
    )
    return PluginInstallationResponse.model_validate(record)


@router.post("/installations/{installation_id}/enable", response_model=dict)
async def enable(installation_id: int, auth: CurrentAuth, session: SessionDep) -> dict:
    record = await authorize_installation(
        auth, session, await session.get(PluginInstallation, installation_id)
    )
    service = PluginService(session)
    try:
        await service.enable(record)
    except ValidationError as exc:
        raise error(422, "config_required", str(exc)) from exc
    plugin = await session.get(Plugin, record.plugin_id)
    runtime = await _sync_runtime(record.bot_id, plugin.slug if plugin else None)
    await AuditService(session).log(
        "plugin.enabled",
        actor_user_id=auth.user.id,
        target_type="installation",
        target_id=installation_id,
        metadata=owner_metadata(auth, scope=record.scope),
    )
    return {
        "state": record.state,
        "runtime": runtime,
        "plugin": plugin.slug if plugin else None,
        "commands": [],
    }


@router.post("/installations/{installation_id}/disable", response_model=dict)
async def disable(installation_id: int, auth: CurrentAuth, session: SessionDep) -> dict:
    record = await authorize_installation(
        auth, session, await session.get(PluginInstallation, installation_id)
    )
    plugin = await session.get(Plugin, record.plugin_id)
    await PluginService(session).disable(record)
    runtime = await _stop_runtime(record, plugin.slug if plugin else "")
    await AuditService(session).log(
        "plugin.disabled",
        actor_user_id=auth.user.id,
        target_type="installation",
        target_id=installation_id,
        metadata=owner_metadata(auth, scope=record.scope),
    )
    return {"state": record.state, "runtime": runtime}


@router.post("/installations/{installation_id}/restart", response_model=dict)
async def restart(installation_id: int, auth: CurrentAuth, session: SessionDep) -> dict:
    record = await authorize_installation(
        auth, session, await session.get(PluginInstallation, installation_id)
    )
    plugin = await session.get(Plugin, record.plugin_id)
    slug = plugin.slug if plugin else ""
    runtime = await _stop_runtime(record, slug)
    runtime = await _sync_runtime(record.bot_id, slug)
    await AuditService(session).log(
        "plugin.restarted",
        actor_user_id=auth.user.id,
        target_type="installation",
        target_id=installation_id,
        metadata=owner_metadata(auth, scope=record.scope),
    )
    return {"runtime": runtime, "state": record.state}


@router.delete("/installations/{installation_id}", status_code=204)
async def uninstall(installation_id: int, auth: CurrentAuth, session: SessionDep):
    record = await authorize_installation(
        auth, session, await session.get(PluginInstallation, installation_id)
    )
    plugin = await session.get(Plugin, record.plugin_id)
    if plugin is not None:
        await _stop_runtime(record, plugin.slug)
    await PluginService(session).uninstall(record)
    await AuditService(session).log(
        "plugin.uninstalled",
        actor_user_id=auth.user.id,
        target_type="installation",
        target_id=installation_id,
        metadata=owner_metadata(auth, scope=record.scope),
    )


@router.get("/installations/{installation_id}/logs", response_model=list[dict])
async def logs(
    installation_id: int, auth: CurrentAuth, session: SessionDep, limit: int = 100
) -> list[dict]:
    await authorize_installation(
        auth, session, await session.get(PluginInstallation, installation_id)
    )
    rows = (
        await session.scalars(
            select(PluginLog)
            .where(PluginLog.installation_id == installation_id)
            .order_by(PluginLog.created_at.desc())
            .limit(min(limit, 500))
        )
    ).all()
    return [
        {
            "id": row.id,
            "level": row.level,
            "message": row.message,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
