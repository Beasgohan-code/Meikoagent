"""Owner control center API – platform administration, enforced server side."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from meiko.api.deps import CurrentAuth, OwnerAuth, SessionDep, error
from meiko.api.schemas import UserResponse
from meiko.db.models import (
    AuditLog,
    BotAccount,
    PluginInstallation,
    RuntimeEvent,
    User,
    Worker,
)
from meiko.services.core import AuditService, BotService, UserService

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
