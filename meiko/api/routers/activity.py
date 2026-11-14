"""Dashboard, live activity, analytics and real-time streams."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from meiko.api.deps import CurrentAuth, SessionDep
from meiko.api.schemas import ActivityItem, DashboardResponse
from meiko.db.models import ActivityEvent, BotAccount, BotGroup, ModerationAction, UsageRecord
from meiko.services.core import ActivityEventService, BotService, DashboardService

router = APIRouter(tags=["activity"])


async def _owned_bot_ids(session, auth) -> list[int]:
    bots = await BotService(session).list_for_owner(auth.user.id)
    return [b.id for b in bots]


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(auth: CurrentAuth, session: SessionDep) -> DashboardResponse:
    data = await DashboardService(session).overview(auth.user.id)
    return DashboardResponse(**data)


@router.get("/activity", response_model=list[ActivityItem])
async def activity(
    auth: CurrentAuth, session: SessionDep, limit: int = 30, bot_id: int | None = None
) -> list[ActivityItem]:
    bot_ids = await _owned_bot_ids(session, auth)
    if bot_id is not None:
        if bot_id not in bot_ids:
            return []
        bot_ids = [bot_id]
    if not bot_ids:
        return []
    stmt = (
        select(ActivityEvent)
        .where(ActivityEvent.bot_id.in_(bot_ids))
        .order_by(ActivityEvent.created_at.desc())
        .limit(min(limit, 200))
    )
    rows = (await session.scalars(stmt)).all()
    return [ActivityItem.model_validate(row) for row in rows]


@router.get("/analytics", response_model=dict)
async def analytics(
    auth: CurrentAuth, session: SessionDep, days: int = 7, bot_id: int | None = None
) -> dict[str, Any]:
    bot_ids = await _owned_bot_ids(session, auth)
    if bot_id is not None:
        if bot_id not in bot_ids:
            return {"bots": 0, "series": [], "moderation": {}, "groups": 0}
        bot_ids = [bot_id]
    days = max(1, min(days, 90))
    if not bot_ids:
        return {
            "bots": 0,
            "series": [],
            "moderation_series": [],
            "moderation": {"by_action": {}, "total": 0, "days": days},
            "groups": 0,
            "empty": True,
            "note": "Connect a bot to start collecting analytics.",
        }
    service = ActivityEventService(session)
    series = await service.series(bot_ids, days=days)
    moderation_series = await service.series(bot_ids, days=days, kind_prefix="moderation")
    since = datetime.utcnow() - timedelta(days=days)
    rows = await session.execute(
        select(ModerationAction.action, func.count())
        .where(ModerationAction.bot_id.in_(bot_ids), ModerationAction.created_at >= since)
        .group_by(ModerationAction.action)
    )
    by_action = {action: count for action, count in rows.all()}
    groups = int(
        await session.scalar(
            select(func.count())
            .select_from(BotGroup)
            .where(BotGroup.bot_id.in_(bot_ids), BotGroup.is_active.is_(True))
        )
        or 0
    )
    total_moderation = int(
        await session.scalar(
            select(func.count())
            .select_from(ModerationAction)
            .where(ModerationAction.bot_id.in_(bot_ids))
        )
        or 0
    )
    return {
        "bots": len(bot_ids),
        "series": series,
        "moderation_series": moderation_series,
        "moderation": {"by_action": by_action, "total": total_moderation, "days": days},
        "groups": groups,
    }


async def _event_stream(request: Request, session_factory, user_id: int) -> AsyncIterator[str]:
    """Server-Sent Events: poll the activity table, never fabricate events."""
    last_seen = 0
    yield "retry: 5000\n\n"
    while True:
        if await request.is_disconnected():
            break
        try:
            async with session_factory() as session:
                bot_ids = await _owned_bot_ids(session, type("A", (), {"user": type("U", (), {"id": user_id})()})())
                if bot_ids:
                    rows = (
                        await session.scalars(
                            select(ActivityEvent)
                            .where(
                                ActivityEvent.bot_id.in_(bot_ids), ActivityEvent.id > last_seen
                            )
                            .order_by(ActivityEvent.id.asc())
                            .limit(50)
                        )
                    ).all()
                    for row in rows:
                        last_seen = max(last_seen, row.id)
                        payload = {
                            "id": row.id,
                            "kind": row.kind,
                            "title": row.title,
                            "severity": row.severity,
                            "bot_id": row.bot_id,
                            "group_id": row.group_id,
                            "created_at": row.created_at.isoformat(),
                        }
                        yield f"event: activity\ndata: {json.dumps(payload)}\n\n"
            yield ": keep-alive\n\n"
        except asyncio.CancelledError:  # pragma: no cover
            break
        except Exception as exc:  # pragma: no cover
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
        await asyncio.sleep(4)


@router.get("/activity/stream")
async def activity_stream(request: Request, auth: CurrentAuth):
    """SSE endpoint for the live dashboard feed."""
    from meiko.db.base import get_sessionmaker

    return StreamingResponse(
        _event_stream(request, get_sessionmaker(), auth.user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/usage", response_model=dict)
async def usage(auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    rows = await session.execute(
        select(UsageRecord.metric, func.sum(UsageRecord.quantity))
        .where(UsageRecord.user_id == auth.user.id)
        .group_by(UsageRecord.metric)
    )
    totals = {metric: float(total or 0) for metric, total in rows.all()}
    bots = await BotService(session).list_for_owner(auth.user.id)
    return {
        "totals": totals,
        "bots": len(bots),
        "clones": sum(1 for b in bots if b.is_clone),
        "note": "Usage is recorded from real platform events only.",
    }
