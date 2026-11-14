"""Developer API: webhooks, docs metadata and message previews."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import select

from meiko.api.deps import CurrentAuth, SessionDep, error
from meiko.api.schemas import WebhookRequest, WebhookResponse
from meiko.config import BOT_API_VERSION
from meiko.db.models import Webhook, WebhookDelivery
from meiko.services.core import AuditService, BotService, NotFound
from meiko.ui import Btn, MessageBuilder, RichBuilder, keyboard, message

router = APIRouter(prefix="/developer", tags=["developer"])


@router.get("/api-reference", response_model=dict)
async def api_reference() -> dict[str, Any]:
    """Machine readable summary of the public API (openapi.json is authoritative)."""
    return {
        "version": "v1",
        "prefix": "/api/v1",
        "bot_api": BOT_API_VERSION,
        "auth": ["Authorization: Bearer <session>", "X-API-Key: mk_..."],
        "groups": {
            "auth": ["POST /auth/telegram", "POST /auth/webapp", "POST /auth/dev", "GET /auth/me"],
            "bots": [
                "GET /bots",
                "POST /bots",
                "PATCH /bots/{id}",
                "POST /bots/{id}/start|stop|restart",
                "POST /bots/{id}/clone",
                "GET /bots/{id}/health",
            ],
            "plugins": [
                "GET /plugins",
                "POST /plugins/{slug}/install",
                "PATCH /plugins/installations/{id}/configure",
                "POST /plugins/installations/{id}/enable|disable|restart",
            ],
            "groups": [
                "GET /groups",
                "PATCH /groups/{id}/settings",
                "GET /groups/{id}/members",
                "GET/POST /groups/{id}/rules",
            ],
            "automation": ["GET/POST /bots/{id}/workflows", "GET/POST /bots/{id}/schedules"],
            "activity": ["GET /dashboard", "GET /activity", "GET /activity/stream", "GET /analytics"],
            "infrastructure": ["GET /infrastructure/runtime", "GET /infrastructure/workers"],
            "owner": ["GET /owner/overview", "GET /owner/users", "GET /owner/logs"],
        },
        "errors": {
            "401": "unauthorized – missing or expired credentials",
            "403": "forbidden – role or ownership check failed",
            "404": "not found – resource missing or outside your tenant",
            "422": "validation error",
            "429": "rate limited – retry_after is returned",
        },
    }


@router.post("/bots/{bot_id}/webhooks", response_model=WebhookResponse, status_code=201)
async def create_webhook(
    bot_id: int, payload: WebhookRequest, auth: CurrentAuth, session: SessionDep
) -> WebhookResponse:
    try:
        await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    record = Webhook(
        bot_id=bot_id,
        url=payload.url,
        events=payload.events,
        enabled=payload.enabled,
        secret_encrypted="",
    )
    session.add(record)
    await session.flush()
    await AuditService(session).log(
        "webhook.created", actor_user_id=auth.user.id, target_type="webhook", target_id=record.id
    )
    return WebhookResponse.model_validate(record)


@router.get("/bots/{bot_id}/webhooks", response_model=list[WebhookResponse])
async def list_webhooks(
    bot_id: int, auth: CurrentAuth, session: SessionDep
) -> list[WebhookResponse]:
    try:
        await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    rows = (
        await session.scalars(select(Webhook).where(Webhook.bot_id == bot_id))
    ).all()
    return [WebhookResponse.model_validate(row) for row in rows]


@router.delete("/bots/{bot_id}/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(
    bot_id: int, webhook_id: int, auth: CurrentAuth, session: SessionDep
):
    try:
        await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    record = await session.scalar(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.bot_id == bot_id)
    )
    if record is None:
        raise error(404, "not_found", "webhook not found")
    await session.delete(record)


@router.get("/bots/{bot_id}/webhooks/{webhook_id}/deliveries", response_model=list[dict])
async def deliveries(
    bot_id: int, webhook_id: int, auth: CurrentAuth, session: SessionDep
) -> list[dict]:
    try:
        await BotService(session).get(bot_id, auth.user.id)
    except NotFound as exc:
        raise error(404, "not_found", "bot not found") from exc
    rows = (
        await session.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == webhook_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(50)
        )
    ).all()
    return [
        {
            "id": row.id,
            "event": row.event,
            "status_code": row.status_code,
            "error": row.error,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/preview", response_model=dict)
async def preview_message(payload: dict[str, Any], auth: CurrentAuth) -> dict:
    """Render UI engine output for the webapp's message designer.

    Only declarative inputs are accepted – nothing is sent to Telegram and no
    code from the browser is executed.
    """
    kind = payload.get("kind", "html")
    if kind == "rich":
        builder = RichBuilder()
        for block in payload.get("blocks", [])[:40]:
            block_type = block.get("type")
            if block_type == "heading":
                builder.heading(str(block.get("text", ""))[:200], int(block.get("size", 2)))
            elif block_type == "paragraph":
                builder.paragraph(str(block.get("text", ""))[:1000])
            elif block_type == "bullets":
                builder.bullets([str(i)[:200] for i in block.get("items", [])][:20])
            elif block_type == "divider":
                builder.divider()
            elif block_type == "quote":
                builder.quote(str(block.get("text", ""))[:500], block.get("credit"))
        rich = builder.build()
        return {
            "kind": "rich",
            "html_preview": builder.to_html(),
            "blocks": len(rich.blocks),
            "payload": rich.model_dump(exclude_none=True),
        }
    builder = MessageBuilder()
    if payload.get("title"):
        builder.title(str(payload["title"])[:200], payload.get("emoji"))
    for line in payload.get("lines", [])[:30]:
        builder.line(str(line)[:500])
    rows = payload.get("buttons") or []
    markup = None
    if rows:
        markup = keyboard(
            [
                [Btn(str(b.get("text", "Open"))[:32], callback_data=b.get("callback_data"), url=b.get("url"), emoji=b.get("emoji"), style=b.get("style"))]
                for b in row
            ]
            for row in rows[:6]
        )
    return {
        "kind": "html",
        "html_preview": builder.build(),
        "plain_preview": builder.plain(),
        "keyboard": markup.model_dump(exclude_none=True) if markup else None,
    }
