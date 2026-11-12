"""Infrastructure endpoints: runtime, workers, database, storage, logs, monitoring."""

from __future__ import annotations

import shutil
import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from meiko import __version__
from meiko.api.deps import CurrentAuth, SessionDep
from meiko.api.schemas import RuntimeHealthResponse
from meiko.cache import get_cache
from meiko.config import BOT_API_VERSION, get_settings
from meiko.db.base import database_health
from meiko.db.models import ActivityEvent, BotAccount, RuntimeEvent, Worker
from meiko.runtime.manager import get_worker_manager
from meiko.services.core import BotService

router = APIRouter(prefix="/infrastructure", tags=["infrastructure"])

_STARTED_AT = time.time()


async def _service_health(auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    from aiogram import Bot

    db = await database_health()
    cache = await get_cache().ping()
    manager = get_worker_manager()
    bots = await BotService(session).list_for_owner(auth.user.id)
    running = [b for b in bots if b.id in manager.running_ids()]
    disk = shutil.disk_usage(get_settings().data_dir)
    try:
        import psutil

        process = psutil.Process()
        memory = process.memory_info().rss
        cpu = psutil.cpu_percent(interval=0.0)
        load = {"cpu_percent": cpu, "rss_bytes": memory}
    except Exception:  # pragma: no cover - psutil optional at runtime
        load = {}
    return {
        "database": db,
        "cache": cache,
        "workers": {
            "local": manager.state.name,
            "running_bots": len(running),
            "capacity": manager.state.capacity,
            "heartbeat_age_seconds": int(time.time() - manager.state.last_heartbeat)
            if manager.state.last_heartbeat
            else None,
        },
        "storage": {
            "data_dir": str(get_settings().data_dir),
            "free_bytes": disk.free,
            "total_bytes": disk.total,
        },
        "process": {"uptime_seconds": int(time.time() - _STARTED_AT), **load},
    }


@router.get("/runtime", response_model=RuntimeHealthResponse)
async def runtime(auth: CurrentAuth, session: SessionDep) -> RuntimeHealthResponse:
    services = await _service_health(auth, session)
    status = "ok" if services["database"]["status"] == "ok" else "degraded"
    return RuntimeHealthResponse(
        status=status,
        services=services,
        version=__version__,
        bot_api=BOT_API_VERSION,
        timestamp=datetime.utcnow(),
    )


@router.get("/workers", response_model=list[dict])
async def workers(auth: CurrentAuth, session: SessionDep) -> list[dict]:
    rows = (await session.scalars(select(Worker).order_by(Worker.id.desc()).limit(50))).all()
    manager = get_worker_manager()
    now = datetime.utcnow()
    return [
        {
            "id": row.id,
            "name": row.name,
            "hostname": row.hostname,
            "pid": row.pid,
            "status": row.status,
            "capacity": row.capacity,
            "current_load": row.current_load,
            "started_at": row.started_at.isoformat(),
            "last_heartbeat_at": row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else None,
            "heartbeat_age_seconds": int((now - row.last_heartbeat_at).total_seconds())
            if row.last_heartbeat_at
            else None,
            "is_local": row.name == manager.state.name,
        }
        for row in rows
    ]


@router.get("/bots", response_model=list[dict])
async def running_bots(auth: CurrentAuth, session: SessionDep) -> list[dict]:
    manager = get_worker_manager()
    bots = await BotService(session).list_for_owner(auth.user.id)
    out = []
    for bot in bots:
        application = manager.state.applications.get(bot.id)
        out.append(
            {
                "id": bot.id,
                "name": bot.name,
                "username": bot.username,
                "status": bot.status,
                "is_running": application is not None,
                "uptime_seconds": int(time.time() - application.started_at)
                if application and application.started_at
                else 0,
                "plugins": len(application.plugins.running_summary())
                if application and application.plugins
                else 0,
                "last_error": bot.last_error,
            }
        )
    return out


@router.get("/logs", response_model=list[dict])
async def logs(
    auth: CurrentAuth, session: SessionDep, limit: int = 100, kind: str | None = None
) -> list[dict]:
    bots = await BotService(session).list_for_owner(auth.user.id)
    bot_ids = [b.id for b in bots]
    stmt = select(RuntimeEvent).order_by(RuntimeEvent.id.desc()).limit(min(limit, 500))
    if bot_ids:
        stmt = stmt.where((RuntimeEvent.bot_id.in_(bot_ids)) | (RuntimeEvent.bot_id.is_(None)))
    else:
        stmt = stmt.where(RuntimeEvent.bot_id.is_(None))
    if kind:
        stmt = stmt.where(RuntimeEvent.kind == kind)
    rows = (await session.scalars(stmt)).all()
    return [
        {
            "id": row.id,
            "bot_id": row.bot_id,
            "kind": row.kind,
            "severity": row.severity,
            "message": row.message,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/monitoring", response_model=dict)
async def monitoring(auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    bots = await BotService(session).list_for_owner(auth.user.id)
    bot_ids = [b.id for b in bots]
    since = datetime.utcnow() - timedelta(hours=24)
    events = 0
    if bot_ids:
        events = int(
            await session.scalar(
                select(func.count())
                .select_from(ActivityEvent)
                .where(ActivityEvent.bot_id.in_(bot_ids), ActivityEvent.created_at >= since)
            )
            or 0
        )
    errors = int(
        await session.scalar(
            select(func.count())
            .select_from(RuntimeEvent)
            .where(
                RuntimeEvent.severity.in_(["error", "critical"]),
                RuntimeEvent.created_at >= since,
            )
        )
        or 0
    )
    manager = get_worker_manager()
    return {
        "window_hours": 24,
        "activity_events": events,
        "runtime_errors": errors,
        "running_bots": len(manager.running_ids()),
        "total_bots": len(bots),
        "worker": manager.state.name,
        "degraded": errors > 0,
    }


@router.get("/database", response_model=dict)
async def database(auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    health = await database_health()
    counts = {}
    from meiko.db.models import (
        GroupMember,
        ModerationAction,
        PluginInstallation,
        User,
    )

    for model in (User, BotAccount, ModerationAction, PluginInstallation, GroupMember):
        counts[model.__tablename__] = int(
            await session.scalar(select(func.count()).select_from(model)) or 0
        )
    settings = get_settings()
    return {
        "health": health,
        "dialect": "postgresql" if "postgres" in settings.resolved_database_url else "sqlite",
        "row_counts": counts,
        "migrations": "alembic",
    }
