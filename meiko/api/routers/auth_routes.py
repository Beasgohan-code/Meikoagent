"""Authentication endpoints: Telegram widget, Mini App, sessions, API keys."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response, status
from sqlalchemy import delete, select

from meiko.api.auth import AuthError, AuthService
from meiko.api.deps import AuthContext, CurrentAuth, SessionDep, client_ip, error
from meiko.api.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    DevLoginRequest,
    SessionResponse,
    TelegramLoginRequest,
    UserResponse,
    WebAppLoginRequest,
)
from meiko.config import get_settings
from meiko.db.models import ApiKey, AuthSession
from meiko.security.crypto import hash_api_key
from meiko.services.core import AuditService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/telegram", response_model=SessionResponse)
async def login_telegram(
    payload: TelegramLoginRequest, request: Request, session: SessionDep
) -> SessionResponse:
    service = AuthService(session)
    try:
        user, token = await service.login_with_telegram(
            payload.model_dump(exclude_none=True),
            user_agent=request.headers.get("user-agent", ""),
            ip=await client_ip(request),
        )
    except AuthError as exc:
        raise error(status.HTTP_401_UNAUTHORIZED, "invalid_telegram_login", str(exc)) from exc
    return SessionResponse(
        token=token, expires_in=get_settings().session_ttl_seconds, user=UserResponse.model_validate(user)
    )


@router.post("/webapp", response_model=SessionResponse)
async def login_webapp(
    payload: WebAppLoginRequest, request: Request, session: SessionDep
) -> SessionResponse:
    service = AuthService(session)
    try:
        user, token = await service.login_with_webapp(
            payload.init_data,
            bot_token=payload.bot_token,
            user_agent=request.headers.get("user-agent", ""),
            ip=await client_ip(request),
        )
    except AuthError as exc:
        raise error(status.HTTP_401_UNAUTHORIZED, "invalid_webapp_login", str(exc)) from exc
    return SessionResponse(
        token=token, expires_in=get_settings().session_ttl_seconds, user=UserResponse.model_validate(user)
    )


@router.post("/dev", response_model=SessionResponse)
async def login_dev(
    payload: DevLoginRequest, request: Request, session: SessionDep
) -> SessionResponse:
    """Local development login – disabled in production."""
    settings = get_settings()
    if not settings.auth_dev_mode or settings.is_production:
        raise error(status.HTTP_403_FORBIDDEN, "dev_login_disabled", "development login is disabled")
    from meiko.services.core import UserService

    user = await UserService(session).get_or_create_telegram_user(
        payload.telegram_id, username=payload.username, first_name=payload.first_name
    )
    token = await AuthService(session).create_session(
        user, user_agent=request.headers.get("user-agent", ""), ip=await client_ip(request)
    )
    return SessionResponse(token=token, expires_in=settings.session_ttl_seconds, user=UserResponse.model_validate(user))


@router.get("/me", response_model=UserResponse)
async def me(auth: CurrentAuth) -> UserResponse:
    return UserResponse.model_validate(auth.user)


@router.post("/logout", status_code=204)
async def logout(
    auth: CurrentAuth,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    if authorization and authorization.lower().startswith("bearer "):
        await AuthService(session).revoke_session(authorization[7:].strip())
    return Response(status_code=204)


@router.post("/logout-all", status_code=200)
async def logout_all(auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    removed = await AuthService(session).revoke_all(auth.user.id)
    return {"revoked": removed}


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(auth: CurrentAuth, session: SessionDep) -> list[ApiKeyResponse]:
    rows = (
        await session.scalars(
            select(ApiKey).where(ApiKey.user_id == auth.user.id, ApiKey.revoked_at.is_(None))
        )
    ).all()
    return [ApiKeyResponse.model_validate(row) for row in rows]


@router.post("/api-keys", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    payload: ApiKeyCreateRequest, auth: CurrentAuth, session: SessionDep
) -> ApiKeyCreatedResponse:
    plaintext, record = await AuthService(session).issue_api_key(
        auth.user, payload.name, payload.scopes
    )
    await AuditService(session).log(
        "api_key.created", actor_user_id=auth.user.id, target_type="api_key", target_id=record.id
    )
    return ApiKeyCreatedResponse(key=plaintext, record=ApiKeyResponse.model_validate(record))


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(key_id: int, auth: CurrentAuth, session: SessionDep) -> Response:
    record = await session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == auth.user.id)
    )
    if record is None:
        raise error(404, "not_found", "API key not found")
    from datetime import datetime

    record.revoked_at = datetime.utcnow()
    await AuditService(session).log(
        "api_key.revoked", actor_user_id=auth.user.id, target_type="api_key", target_id=key_id
    )
    return Response(status_code=204)


@router.get("/sessions", response_model=list[dict])
async def list_sessions(auth: CurrentAuth, session: SessionDep) -> list[dict]:
    rows = (
        await session.scalars(select(AuthSession).where(AuthSession.user_id == auth.user.id))
    ).all()
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "revoked": row.revoked_at is not None,
            "user_agent": row.user_agent,
            "ip": row.ip_address,
        }
        for row in rows
    ]
