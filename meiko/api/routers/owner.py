"""Owner control center API – platform administration, enforced server side."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from meiko.api.deps import OwnerAuth, SessionDep, error
from meiko.api.schemas import UserResponse
from meiko.db.models import (
    AuditLog,
    BotAccount,
    PluginInstallation,
    RuntimeEvent,
    User,
    Worker,
)
from meiko.services.core import AuditService, UserService

router = APIRouter(prefix="/owner", tags=["owner"])


@router.get("/overview", response_model=dict)
async def overview(auth: OwnerAuth, session: SessionDep) -> dict[str, Any]:
    users = int(await session.scalar(select(func.count()).select_from(User)) or 0)
    bots = int(await session.scalar(select(func.count()).select_from(BotAccount)) or 0)
    running = int(
        await session.scalar(
            select(func.count()).select_from(BotAccount).where(BotAccount.status == "running")
        )
        or 0
    )
    plugins = int(await session.scalar(select(func.count()).select_from(PluginInstallation)) or 0)
    errors = int(
        await session.scalar(
            select(func.count())
            .select_from(RuntimeEvent)
            .where(RuntimeEvent.severity.in_(["error", "critical"]))
        )
        or 0
    )
    workers = (
        await session.scalars(select(Worker).order_by(Worker.id.desc()).limit(10))
    ).all()
    return {
        "users": users,
        "bots": bots,
        "running_bots": running,
        "plugin_installations": plugins,
        "runtime_errors": errors,
        "workers": [
            {
                "id": w.id,
                "name": w.name,
                "status": w.status,
                "load": f"{w.current_load}/{w.capacity}",
            }
            for w in workers
        ],
    }


@router.get("/users", response_model=list[UserResponse])
async def users(
    auth: OwnerAuth, session: SessionDep, search: str | None = None, limit: int = 50
) -> list[UserResponse]:
    stmt = select(User).order_by(User.id.desc()).limit(min(limit, 200))
    if search:
        like = f"%{search.lstrip('@')}%"
        stmt = stmt.where((User.username.ilike(like)) | (User.first_name.ilike(like)))
    rows = (await session.scalars(stmt)).all()
    return [UserResponse.model_validate(row) for row in rows]


@router.post("/users/{user_id}/role", response_model=UserResponse)
async def set_role(
    user_id: int, role: str, auth: OwnerAuth, session: SessionDep
) -> UserResponse:
    if role not in ("user", "admin", "owner"):
        raise error(422, "invalid_role", "role must be user, admin or owner")
    user = await UserService(session).set_role(user_id, role, actor_is_owner=True)
    await AuditService(session).log(
        "owner.role_changed", actor_user_id=auth.user.id, target_type="user", target_id=user_id,
        metadata={"role": role},
    )
    return UserResponse.model_validate(user)


@router.post("/users/{user_id}/status", response_model=UserResponse)
async def set_status(
    user_id: int, status: str, auth: OwnerAuth, session: SessionDep
) -> UserResponse:
    if status not in ("active", "restricted"):
        raise error(422, "invalid_status", "status must be active or restricted")
    user = await UserService(session).get(user_id)
    user.status = status
    await AuditService(session).log(
        "owner.user_status", actor_user_id=auth.user.id, target_type="user", target_id=user_id,
        metadata={"status": status},
    )
    return UserResponse.model_validate(user)


@router.get("/bots", response_model=list[dict])
async def all_bots(auth: OwnerAuth, session: SessionDep, limit: int = 100) -> list[dict]:
    rows = (
        await session.scalars(select(BotAccount).order_by(BotAccount.id.desc()).limit(min(limit, 500)))
    ).all()
    return [
        {
            "id": row.id,
            "owner_id": row.owner_id,
            "name": row.name,
            "username": row.username,
            "status": row.status,
            "is_clone": row.is_clone,
            "token_valid": row.token_valid,
            "last_error": row.last_error,
            "worker_id": row.worker_id,
        }
        for row in rows
    ]


@router.get("/audit", response_model=list[dict])
async def audit_log(
    auth: OwnerAuth, session: SessionDep, limit: int = 100
) -> list[dict]:
    rows = (
        await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500)))
    ).all()
    return [
        {
            "id": row.id,
            "actor_user_id": row.actor_user_id,
            "actor_type": row.actor_type,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "ip_address": row.ip_address,
            "created_at": row.created_at.isoformat(),
            "metadata": row.metadata_json,
        }
        for row in rows
    ]


@router.get("/security-events", response_model=list[dict])
async def security_events(auth: OwnerAuth, session: SessionDep) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=7)
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(
                AuditLog.created_at >= since,
                AuditLog.action.in_(["auth.login", "owner.role_changed", "owner.user_status"]),
            )
            .order_by(AuditLog.id.desc())
            .limit(100)
        )
    ).all()
    return [
        {
            "id": row.id,
            "action": row.action,
            "actor_user_id": row.actor_user_id,
            "ip_address": row.ip_address,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/plugins/{slug}/approve", response_model=dict)
async def approve_plugin(slug: str, auth: OwnerAuth, session: SessionDep) -> dict:
    from meiko.db.models import Plugin

    plugin = await session.scalar(select(Plugin).where(Plugin.slug == slug))
    if plugin is None:
        raise error(404, "not_found", "plugin not found")
    plugin.status = "approved"
    await AuditService(session).log(
        "owner.plugin_approved", actor_user_id=auth.user.id, target_type="plugin", target_id=slug
    )
    return {"slug": slug, "status": plugin.status}


@router.post("/plugins/{slug}/block", response_model=dict)
async def block_plugin(slug: str, auth: OwnerAuth, session: SessionDep) -> dict:
    from meiko.db.models import Plugin

    plugin = await session.scalar(select(Plugin).where(Plugin.slug == slug))
    if plugin is None:
        raise error(404, "not_found", "plugin not found")
    plugin.status = "blocked"
    installs = (
        await session.scalars(
            select(PluginInstallation).where(PluginInstallation.plugin_id == plugin.id)
        )
    ).all()
    for install in installs:
        install.state = "disabled"
    await AuditService(session).log(
        "owner.plugin_blocked", actor_user_id=auth.user.id, target_type="plugin", target_id=slug
    )
    return {"slug": slug, "status": plugin.status, "disabled_installations": len(installs)}


@router.post("/maintenance", response_model=dict)
async def maintenance(auth: OwnerAuth, enabled: bool = False) -> dict:
    from meiko.api.state import set_maintenance

    set_maintenance(enabled)
    return {"maintenance_mode": enabled}


# --------------------------------------------------------------- capabilities
CAPABILITIES = {
    "code_exec": {
        "label": "Code execution",
        "description": "Run python/node/bash snippets from chat inside the code_runner sandbox.",
        "risk": "critical",
        "plugin": "code_runner",
    },
    "remote_fetch": {
        "label": "Outbound HTTP",
        "description": "Let internet-facing plugins (integrations, music lookup) call external services.",
        "risk": "high",
        "plugin": "integrations",
    },
    "file_upload": {
        "label": "File storage",
        "description": "Persist uploaded files inside the plugin workspace quota.",
        "risk": "medium",
        "plugin": "file_store",
    },
    "media_encode": {
        "label": "Video encoding",
        "description": "Spawn ffmpeg for re-encodes (CPU-heavy, sizes the encoder workspace).",
        "risk": "high",
        "plugin": "encoder",
    },
    "bulk_transfer": {
        "label": "Bulk mirror/leech",
        "description": "Download remote files with the leech engine and re-upload them.",
        "risk": "high",
        "plugin": "leech",
    },
    "public_sharing": {
        "label": "Public sharing",
        "description": "Allow signed share links (filestore) and protected link release (linkshare).",
        "risk": "medium",
        "plugin": "filestore",
    },
}


@router.get("/capabilities", response_model=list[dict])
async def capabilities(auth: OwnerAuth, session: SessionDep) -> list[dict[str, Any]]:
    """Owner-visible state of every gated capability."""
    from meiko.plugins.support import CapabilityGate

    out = []
    for name, meta in CAPABILITIES.items():
        state = await CapabilityGate(name).state(refresh=True)
        out.append({**meta, "capability": name, **state.to_dict()})
    return out


@router.post("/capabilities/{capability}", response_model=dict)
async def set_capability(
    capability: str,
    auth: OwnerAuth,
    session: SessionDep,
    enabled: bool | None = None,
    allowed_telegram_ids: str | None = None,
    allowed_bot_ids: str | None = None,
    max_runs_per_day: int | None = None,
) -> dict[str, Any]:
    """Open, close or narrow a dangerous capability. Owner only, fully audited."""
    from meiko.plugins.support import CapabilityGate

    if capability not in CAPABILITIES:
        raise error(404, "not_found", "unknown capability")

    def parse_ids(raw: str | None) -> list[int] | None:
        if raw is None:
            return None
        return [int(part) for part in raw.replace(" ", "").split(",") if part.strip().lstrip("-").isdigit()]

    state = await CapabilityGate(capability).set_state(
        enabled=enabled,
        allowed_telegram_ids=parse_ids(allowed_telegram_ids),
        allowed_bot_ids=parse_ids(allowed_bot_ids),
        max_runs_per_day=max_runs_per_day,
        actor_user_id=auth.user.id,
    )
    await AuditService(session).log(
        "owner.capability_changed",
        actor_user_id=auth.user.id,
        target_type="capability",
        target_id=capability,
        metadata={
            "enabled": state.enabled,
            "allowed_telegram_ids": state.allowed_telegram_ids,
            "allowed_bot_ids": state.allowed_bot_ids,
        },
    )
    return {"capability": capability, **CAPABILITIES[capability], **state.to_dict()}


# ------------------------------------------------------- global plugin scope
# Global installations are platform-wide: they apply to every bot unless that bot
# has its own row.  They are therefore an owner capability, with their own
# endpoints so the ordinary marketplace client never needs a global code path.
@router.get("/plugins/global", response_model=list[dict])
async def global_installations(auth: OwnerAuth, session: SessionDep) -> list[dict[str, Any]]:
    """Every global-scope installation, with its live runtime state."""
    from meiko.db.models import Plugin
    from meiko.plugins.manager import runtime_status
    from meiko.runtime.manager import get_worker_manager

    from meiko.plugins.context import PluginScope

    rows = (
        await session.scalars(
            select(PluginInstallation).where(PluginInstallation.bot_id.is_(None))
        )
    ).all()
    catalogue = {plugin.id: plugin for plugin in (await session.scalars(select(Plugin))).all()}
    manager = get_worker_manager()
    out: list[dict[str, Any]] = []
    for row in rows:
        plugin = catalogue.get(row.plugin_id)
        live = any(
            application.plugins is not None
            and plugin is not None
            and application.plugins.is_running(plugin.slug, PluginScope(bot_id=bot_id))
            for bot_id, application in manager.state.applications.items()
        )
        out.append(
            {
                "id": row.id,
                "slug": plugin.slug if plugin else None,
                "name": plugin.name if plugin else None,
                "state": row.state,
                "version": row.version,
                "scope": row.scope,
                "config": row.config or {},
                "running_somewhere": live,
                "status": runtime_status(
                    plugin=plugin,
                    record=row,
                    live=live,
                    bot_running=bool(manager.state.applications),
                )
                if plugin
                else "blocked",
            }
        )
    return out


@router.post("/plugins/global/{slug}/reconcile", response_model=dict)
async def reconcile_global_plugin(slug: str, auth: OwnerAuth, session: SessionDep) -> dict[str, Any]:
    """Re-apply a global plugin to every running bot, right now.

    "Global changes apply on next start" was the old behaviour and it silently
    left running bots on stale plugin sets.  This reconciles them immediately and
    reports per-bot results, including the bots it could not touch.
    """
    from meiko.db.models import Plugin
    from meiko.runtime.manager import get_worker_manager

    plugin = await session.scalar(select(Plugin).where(Plugin.slug == slug))
    if plugin is None:
        raise error(404, "not_found", "plugin not found")
    manager = get_worker_manager()
    results: dict[str, Any] = {}
    for bot_id, application in list(manager.state.applications.items()):
        if application.plugins is None:
            results[str(bot_id)] = {"applied": False, "reason": "plugins not loaded"}
            continue
        results[str(bot_id)] = {"applied": True, "states": await application.load_plugins()}
    await AuditService(session).log(
        "owner.plugin_global_reconciled",
        actor_user_id=auth.user.id,
        target_type="plugin",
        target_id=slug,
        metadata={"bots": list(results), "applied": sum(
            1 for value in results.values() if value.get("applied")
        )},
    )
    return {
        "slug": slug,
        "bots": results,
        "applied": sum(1 for value in results.values() if value.get("applied")),
        "skipped": sum(1 for value in results.values() if not value.get("applied")),
    }


@router.get("/plugins/overview", response_model=dict)
async def plugin_overview(auth: OwnerAuth, session: SessionDep) -> dict[str, Any]:
    """Owner-facing plugin inventory: catalogue, approvals, activations, failures."""
    from meiko.db.models import Plugin
    from meiko.plugins.base import registry as plugin_registry
    from meiko.plugins.manager import missing_permissions, permission_changes, runtime_status

    plugins = (await session.scalars(select(Plugin).order_by(Plugin.slug))).all()
    installs = (await session.scalars(select(PluginInstallation))).all()
    by_plugin: dict[int, list[PluginInstallation]] = {}
    for row in installs:
        by_plugin.setdefault(row.plugin_id, []).append(row)
    implemented = set(plugin_registry.slugs())
    rows: list[dict[str, Any]] = []
    for plugin in plugins:
        rows_of = by_plugin.get(plugin.id, [])
        rows.append(
            {
                "slug": plugin.slug,
                "name": plugin.name,
                "version": plugin.version,
                "category": plugin.category,
                "status": plugin.status,
                "implemented": plugin.slug in implemented,
                "permissions": list(plugin.permissions or []),
                "missing_permissions": missing_permissions(plugin),
                "pending_permission_changes": permission_changes(plugin),
                "dependencies": list(plugin.dependencies or []),
                "installations": {
                    "global": sum(1 for r in rows_of if r.bot_id is None),
                    "bot": sum(1 for r in rows_of if r.bot_id is not None and r.group_id is None),
                    "group": sum(1 for r in rows_of if r.group_id is not None),
                },
                "states": {
                    state: sum(1 for r in rows_of if r.state == state)
                    for state in sorted({r.state for r in rows_of})
                },
            }
        )
    return {
        "plugins": rows,
        "totals": {
            "catalogue": len(plugins),
            "implemented": sum(1 for r in rows if r["implemented"]),
            "approved": sum(1 for r in plugins if r.status == "approved"),
            "blocked": sum(1 for r in plugins if r.status == "blocked"),
            "pending": sum(1 for r in plugins if r.status == "pending"),
            "installations": len(installs),
            "global_installations": sum(1 for r in installs if r.bot_id is None),
        },
    }
