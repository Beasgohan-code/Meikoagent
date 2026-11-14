"""Group and moderation endpoints: settings, members, history, rules, reports."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import func, select

from meiko.api.deps import CurrentAuth, SessionDep, error
from meiko.api.schemas import (
    GroupResponse,
    GroupSettingsUpdate,
    ModerationActionResponse,
    RuleRequest,
    RuleResponse,
)
from meiko.db.models import (
    Appeal,
    BotGroup,
    GroupMember,
    GroupRule,
    GroupSettings,
    ModerationAction,
    Report,
    Warning,
)
from meiko.moderation.antispam import AntiSpamPolicy
from meiko.moderation.rules import Rule
from meiko.services.core import AuditService, BotService, GroupService, NotFound

router = APIRouter(prefix="/groups", tags=["groups"])


async def _owned_group(session, auth, group_id: int) -> BotGroup:
    group = await session.get(BotGroup, group_id)
    if group is None:
        raise error(404, "not_found", "group not found")
    try:
        await BotService(session).get(group.bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "group not found") from exc
    return group


@router.get("", response_model=list[GroupResponse])
async def list_groups(
    auth: CurrentAuth, session: SessionDep, bot_id: int | None = None
) -> list[GroupResponse]:
    if bot_id is not None:
        try:
            await BotService(session).get(bot_id, auth.user.id)
        except NotFound as exc:
            raise error(404, "not_found", "bot not found") from exc
        rows = await GroupService(session).list_for_bot(bot_id)
    else:
        bots = await BotService(session).list_for_owner(auth.user.id)
        bot_ids = [b.id for b in bots]
        if not bot_ids:
            return []
        rows = (
            await session.scalars(
                select(BotGroup).where(BotGroup.bot_id.in_(bot_ids)).order_by(BotGroup.title)
            )
        ).all()
    return [GroupResponse.model_validate(row) for row in rows]


@router.get("/{group_id}", response_model=dict)
async def group_detail(group_id: int, auth: CurrentAuth, session: SessionDep) -> dict:
    group = await _owned_group(session, auth, group_id)
    settings = await GroupService(session).settings(group_id)
    members = int(
        await session.scalar(
            select(func.count()).select_from(GroupMember).where(GroupMember.group_id == group_id)
        )
        or 0
    )
    actions = int(
        await session.scalar(
            select(func.count())
            .select_from(ModerationAction)
            .where(ModerationAction.group_id == group_id)
        )
        or 0
    )
    return {
        **GroupResponse.model_validate(group).model_dump(),
        "settings": {
            "welcome_enabled": settings.welcome_enabled,
            "welcome_text": settings.welcome_text,
            "goodbye_enabled": settings.goodbye_enabled,
            "goodbye_text": settings.goodbye_text,
            "verification_enabled": settings.verification_enabled,
            "auto_delete_seconds": settings.auto_delete_seconds,
            "slow_mode_seconds": settings.slow_mode_seconds,
            "antispam": AntiSpamPolicy.from_dict(settings.antispam or {}).to_dict(),
            "moderation": settings.moderation,
            "logging_enabled": settings.logging_enabled,
        },
        "tracked_members": members,
        "moderation_actions": actions,
    }


@router.patch("/{group_id}/settings", response_model=dict)
async def update_settings(
    group_id: int, payload: GroupSettingsUpdate, auth: CurrentAuth, session: SessionDep
) -> dict:
    group = await _owned_group(session, auth, group_id)
    values = payload.model_dump(exclude_none=True)
    if "antispam" in values:
        values["antispam"] = AntiSpamPolicy.from_dict(values["antispam"]).to_dict()
    settings = await GroupService(session).update_settings(group_id, **values)
    await AuditService(session).log(
        "group.settings_updated", actor_user_id=auth.user.id, target_type="group", target_id=group_id,
        metadata={"keys": sorted(values)},
    )
    return {"group_id": group.id, "updated": sorted(values)}


@router.get("/{group_id}/members", response_model=list[dict])
async def members(
    group_id: int,
    auth: CurrentAuth,
    session: SessionDep,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    await _owned_group(session, auth, group_id)
    stmt = select(GroupMember).where(GroupMember.group_id == group_id).order_by(GroupMember.id)
    if search:
        like = f"%{search.lstrip('@')}%"
        stmt = stmt.where(
            (GroupMember.username.ilike(like)) | (GroupMember.first_name.ilike(like))
        )
    rows = (await session.scalars(stmt.limit(min(limit, 200)).offset(offset))).all()
    return [
        {
            "id": row.id,
            "telegram_id": row.telegram_id,
            "username": row.username,
            "first_name": row.first_name,
            "is_admin": row.is_admin,
            "is_trusted": row.is_trusted,
            "is_muted": row.is_muted,
            "warnings_count": row.warnings_count,
            "messages_count": row.messages_count,
            "joined_at": row.joined_at.isoformat() if row.joined_at else None,
            "note": row.note,
            "available": True,
        }
        for row in rows
    ]


@router.get("/{group_id}/moderation", response_model=list[ModerationActionResponse])
async def moderation_history(
    group_id: int, auth: CurrentAuth, session: SessionDep, limit: int = 50, offset: int = 0
) -> list[ModerationActionResponse]:
    group = await _owned_group(session, auth, group_id)
    from meiko.services.core import ModerationService

    rows = await ModerationService(session).history(
        group.bot_id, group_id=group_id, limit=min(limit, 200), offset=offset
    )
    return [ModerationActionResponse.model_validate(row) for row in rows]


@router.get("/{group_id}/rules", response_model=list[RuleResponse])
async def list_rules(group_id: int, auth: CurrentAuth, session: SessionDep) -> list[RuleResponse]:
    group = await _owned_group(session, auth, group_id)
    rows = (
        await session.scalars(
            select(GroupRule)
            .where(GroupRule.group_id == group_id)
            .order_by(GroupRule.priority)
        )
    ).all()
    return [RuleResponse.model_validate(row) for row in rows]


@router.post("/{group_id}/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    group_id: int, payload: RuleRequest, auth: CurrentAuth, session: SessionDep
) -> RuleResponse:
    group = await _owned_group(session, auth, group_id)
    candidate = Rule(
        id=None,
        name=payload.name,
        event=payload.event,
        conditions=payload.conditions,
        actions=payload.actions,
        priority=payload.priority,
        enabled=payload.enabled,
    )
    errors = candidate.validate()
    if errors:
        raise error(422, "invalid_rule", "; ".join(errors))
    record = GroupRule(
        bot_id=group.bot_id,
        group_id=group_id,
        name=payload.name,
        event=payload.event,
        conditions=payload.conditions,
        actions=payload.actions,
        priority=payload.priority,
        enabled=payload.enabled,
        created_by=auth.user.telegram_id,
    )
    session.add(record)
    await session.flush()
    await AuditService(session).log(
        "group.rule_created", actor_user_id=auth.user.id, target_type="rule", target_id=record.id
    )
    return RuleResponse.model_validate(record)


@router.delete("/{group_id}/rules/{rule_id}", status_code=204)
async def delete_rule(group_id: int, rule_id: int, auth: CurrentAuth, session: SessionDep):
    await _owned_group(session, auth, group_id)
    record = await session.scalar(
        select(GroupRule).where(GroupRule.id == rule_id, GroupRule.group_id == group_id)
    )
    if record is None:
        raise error(404, "not_found", "rule not found")
    await session.delete(record)


@router.get("/{group_id}/warnings", response_model=list[dict])
async def warnings(group_id: int, auth: CurrentAuth, session: SessionDep) -> list[dict]:
    await _owned_group(session, auth, group_id)
    rows = (
        await session.scalars(
            select(Warning)
            .where(Warning.group_id == group_id, Warning.active.is_(True))
            .order_by(Warning.created_at.desc())
            .limit(200)
        )
    ).all()
    return [
        {
            "id": row.id,
            "telegram_id": row.telegram_id,
            "reason": row.reason,
            "created_at": row.created_at.isoformat(),
            "moderator_id": row.moderator_id,
        }
        for row in rows
    ]


@router.get("/{group_id}/reports", response_model=list[dict])
async def reports(group_id: int, auth: CurrentAuth, session: SessionDep) -> list[dict]:
    await _owned_group(session, auth, group_id)
    rows = (
        await session.scalars(
            select(Report).where(Report.group_id == group_id).order_by(Report.created_at.desc()).limit(100)
        )
    ).all()
    return [
        {
            "id": row.id,
            "reporter_id": row.reporter_id,
            "target_id": row.target_id,
            "reason": row.reason,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.patch("/{group_id}/reports/{report_id}", response_model=dict)
async def resolve_report(
    group_id: int,
    report_id: int,
    auth: CurrentAuth,
    session: SessionDep,
    status_value: str = "resolved",
) -> dict:
    await _owned_group(session, auth, group_id)
    if status_value not in ("open", "resolved", "dismissed"):
        raise error(422, "invalid_status", "status must be open, resolved or dismissed")
    from datetime import datetime

    report = await session.scalar(
        select(Report).where(Report.id == report_id, Report.group_id == group_id)
    )
    if report is None:
        raise error(404, "not_found", "report not found")
    report.status = status_value
    if status_value != "open":
        report.resolved_at = datetime.utcnow()
        report.resolved_by = auth.user.telegram_id
    return {"id": report.id, "status": report.status}


@router.get("/appeals/all", response_model=list[dict])
async def appeals(auth: CurrentAuth, session: SessionDep) -> list[dict]:
    bots = await BotService(session).list_for_owner(auth.user.id)
    bot_ids = [b.id for b in bots]
    if not bot_ids:
        return []
    rows = (
        await session.scalars(
            select(Appeal).where(Appeal.bot_id.in_(bot_ids)).order_by(Appeal.created_at.desc()).limit(100)
        )
    ).all()
    return [
        {
            "id": row.id,
            "telegram_id": row.telegram_id,
            "message": row.message,
            "status": row.status,
            "decision": row.decision,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
