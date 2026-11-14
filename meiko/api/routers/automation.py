"""Automation Studio endpoints: workflows and scheduled tasks."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import select

from meiko.api.deps import CurrentAuth, SessionDep, error
from meiko.api.schemas import ScheduleRequest, ScheduleResponse, WorkflowRequest, WorkflowResponse
from meiko.db.models import ScheduledTask, utcnow, Workflow
from meiko.plugins.builtin.automation import validate_workflow
from meiko.services.core import AuditService, BotService, NotFound

router = APIRouter(prefix="/bots/{bot_id}", tags=["automation"])


async def _owned_bot(session, auth, bot_id: int):
    try:
        return await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc


@router.get("/workflows", response_model=list[WorkflowResponse])
async def list_workflows(
    bot_id: int, auth: CurrentAuth, session: SessionDep
) -> list[WorkflowResponse]:
    await _owned_bot(session, auth, bot_id)
    rows = (
        await session.scalars(
            select(Workflow).where(Workflow.bot_id == bot_id).order_by(Workflow.id.desc())
        )
    ).all()
    return [WorkflowResponse.model_validate(row) for row in rows]


@router.post("/workflows", response_model=WorkflowResponse, status_code=201)
async def create_workflow(
    bot_id: int, payload: WorkflowRequest, auth: CurrentAuth, session: SessionDep
) -> WorkflowResponse:
    await _owned_bot(session, auth, bot_id)
    errors = validate_workflow(payload.model_dump())
    if errors:
        raise error(422, "invalid_workflow", "; ".join(errors))
    record = Workflow(
        bot_id=bot_id,
        name=payload.name,
        description=payload.description,
        enabled=payload.enabled,
        trigger=payload.trigger,
        conditions=payload.conditions,
        actions=payload.actions,
    )
    session.add(record)
    await session.flush()
    await AuditService(session).log(
        "workflow.created", actor_user_id=auth.user.id, target_type="workflow",
        target_id=record.id,
    )
    return WorkflowResponse.model_validate(record)


@router.patch("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    bot_id: int,
    workflow_id: int,
    payload: WorkflowRequest,
    auth: CurrentAuth,
    session: SessionDep,
) -> WorkflowResponse:
    await _owned_bot(session, auth, bot_id)
    record = await session.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.bot_id == bot_id)
    )
    if record is None:
        raise error(404, "not_found", "workflow not found")
    errors = validate_workflow(payload.model_dump())
    if errors:
        raise error(422, "invalid_workflow", "; ".join(errors))
    record.name = payload.name
    record.description = payload.description
    record.enabled = payload.enabled
    record.trigger = payload.trigger
    record.conditions = payload.conditions
    record.actions = payload.actions
    record.version = int(record.version or 1) + 1
    await session.flush()
    return WorkflowResponse.model_validate(record)


@router.delete("/workflows/{workflow_id}", status_code=204)
async def delete_workflow(
    bot_id: int, workflow_id: int, auth: CurrentAuth, session: SessionDep
):
    await _owned_bot(session, auth, bot_id)
    record = await session.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.bot_id == bot_id)
    )
    if record is None:
        raise error(404, "not_found", "workflow not found")
    await session.delete(record)


@router.get("/schedules", response_model=list[ScheduleResponse])
async def list_schedules(
    bot_id: int, auth: CurrentAuth, session: SessionDep
) -> list[ScheduleResponse]:
    await _owned_bot(session, auth, bot_id)
    rows = (
        await session.scalars(
            select(ScheduledTask)
            .where(ScheduledTask.bot_id == bot_id)
            .order_by(ScheduledTask.id.desc())
        )
    ).all()
    return [ScheduleResponse.model_validate(row) for row in rows]


@router.post("/schedules", response_model=ScheduleResponse, status_code=201)
async def create_schedule(
    bot_id: int, payload: ScheduleRequest, auth: CurrentAuth, session: SessionDep
) -> ScheduleResponse:
    await _owned_bot(session, auth, bot_id)
    if not payload.cron and not payload.run_at:
        raise error(422, "invalid_schedule", "provide either cron or run_at")
    if payload.cron and not validate_cron(payload.cron):
        raise error(422, "invalid_cron", "cron must have 5 fields: minute hour day month weekday")
    record = ScheduledTask(
        bot_id=bot_id,
        group_id=payload.group_id,
        name=payload.name,
        kind=payload.kind,
        cron=payload.cron,
        run_at=payload.run_at or (utcnow() + timedelta(minutes=1)),
        next_run_at=payload.run_at or utcnow() + timedelta(minutes=1),
        payload=payload.payload,
        status="pending",
    )
    session.add(record)
    await session.flush()
    return ScheduleResponse.model_validate(record)


@router.post("/schedules/{task_id}/cancel", response_model=ScheduleResponse)
async def cancel_schedule(
    bot_id: int, task_id: int, auth: CurrentAuth, session: SessionDep
) -> ScheduleResponse:
    await _owned_bot(session, auth, bot_id)
    record = await session.scalar(
        select(ScheduledTask).where(ScheduledTask.id == task_id, ScheduledTask.bot_id == bot_id)
    )
    if record is None:
        raise error(404, "not_found", "schedule not found")
    record.status = "cancelled"
    await session.flush()
    return ScheduleResponse.model_validate(record)


def validate_cron(expression: str) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        return False
    import re

    pattern = re.compile(r"^[\d*/,\-]+$")
    return all(pattern.match(field) for field in fields)
