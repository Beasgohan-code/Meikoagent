"""Bot template catalogue – read-only, used by the webapp "Create bot" flow."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from meiko.api.deps import CurrentAuth, SessionDep
from meiko.api.schemas import TemplateResponse
from meiko.db.models import BotTemplate

router = APIRouter(prefix="/bot-templates", tags=["bots"])


@router.get("", response_model=list[TemplateResponse])
async def list_templates(auth: CurrentAuth, session: SessionDep) -> list[TemplateResponse]:
    """Public templates plus the caller's own private templates."""
    rows = (
        await session.scalars(
            select(BotTemplate).order_by(BotTemplate.category, BotTemplate.name)
        )
    ).all()
    return [
        TemplateResponse.model_validate(row)
        for row in rows
        if row.is_public or row.owner_id in (None, auth.user.id)
    ]
