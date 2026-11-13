"""Authentication: Telegram Login Widget + Mini App ``initData`` + sessions.

Both flows are validated with HMAC-SHA256 against the bot token Telegram signed
with, exactly as documented:
``secret_key = SHA256(bot_token)`` for the Login Widget and
``secret_key = HMAC_SHA256("WebAppData", bot_token)`` for ``initData``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from meiko.config import get_settings
from meiko.db.models import AuthSession, User
from meiko.logging_setup import get_logger
from meiko.security.crypto import hash_api_key, new_api_key, sign_session
from meiko.services.core import AuditService, UserService

log = get_logger("api.auth")

MAX_AUTH_AGE = 300  # seconds a login payload stays valid
MAX_CLOCK_SKEW = 60  # seconds a client clock may run ahead of ours


class AuthError(ValueError):
    """Authentication payload failed validation."""


def _check_freshness(auth_date: int, max_age: int, label: str = "login payload") -> None:
    """Reject stale *and* future-dated payloads.

    A missing ``auth_date`` is itself suspicious — a payload with no timestamp can
    never expire — so it is rejected rather than treated as "now".
    """
    now = int(time.time())
    if not auth_date:
        raise AuthError("missing auth_date")
    if max_age and now - auth_date > max_age:
        raise AuthError(f"{label} expired")
    if auth_date - now > MAX_CLOCK_SKEW:
        raise AuthError(f"{label} is dated in the future")


def check_login_widget(data: dict[str, Any], bot_token: str, max_age: int = 86400) -> dict[str, Any]:
    """Validate a Telegram Login Widget payload."""
    received_hash = data.get("hash")
    if not received_hash:
        raise AuthError("missing hash")
    check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(data.items())
        if key not in ("hash",) and value is not None
    )
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(received_hash)):
        raise AuthError("signature mismatch")
    _check_freshness(int(data.get("auth_date", 0) or 0), max_age)
    return data


def check_webapp_init_data(init_data: str, bot_token: str, max_age: int = 3600) -> dict[str, Any]:
    """Validate ``WebApp.initData`` from a Telegram Mini App."""
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise AuthError("missing hash")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("signature mismatch")
    _check_freshness(int(pairs.get("auth_date", 0) or 0), max_age, label="initData")
    user_payload = pairs.get("user")
    if user_payload:
        try:
            pairs["user"] = json.loads(user_payload)
        except json.JSONDecodeError as exc:
            raise AuthError("malformed user payload") from exc
    return pairs


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve_bot_token(self, bot_token: str | None = None) -> str:
        """The token Telegram signed with: explicit, platform bot, or first valid bot."""
        from meiko.config import get_settings as _settings
        from meiko.db.models import BotAccount
        from meiko.security.crypto import decrypt_secret

        if bot_token:
            return bot_token
        configured = _settings().bot_token
        if configured:
            return configured
        row = await self.session.scalar(
            select(BotAccount).where(BotAccount.token_valid.is_(True)).order_by(BotAccount.id)
        )
        if row is None:
            raise AuthError("no bot token is configured for Telegram login")
        return decrypt_secret(row.token_encrypted)

    async def login_with_telegram(
        self,
        payload: dict[str, Any],
        *,
        bot_token: str | None = None,
        user_agent: str = "",
        ip: str = "",
    ) -> tuple[User, str]:
        token = await self.resolve_bot_token(bot_token)
        data = check_login_widget(payload, token)
        user = await UserService(self.session).get_or_create_telegram_user(
            int(data["id"]),
            username=data.get("username"),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            photo_url=data.get("photo_url"),
        )
        if user.status != "active":
            raise AuthError("account is restricted")
        token_value = await self.create_session(user, user_agent=user_agent, ip=ip)
        await AuditService(self.session).log(
            "auth.login", actor_user_id=user.id, actor_type="user", ip_address=ip,
            metadata={"method": "telegram_widget"},
        )
        return user, token_value

    async def login_with_webapp(
        self,
        init_data: str,
        *,
        bot_token: str | None = None,
        user_agent: str = "",
        ip: str = "",
    ) -> tuple[User, str]:
        token = await self.resolve_bot_token(bot_token)
        data = check_webapp_init_data(init_data, token)
        user_payload = data.get("user") or {}
        if not isinstance(user_payload, dict) or "id" not in user_payload:
            raise AuthError("initData has no user")
        user = await UserService(self.session).get_or_create_telegram_user(
            int(user_payload["id"]),
            username=user_payload.get("username"),
            first_name=user_payload.get("first_name", ""),
            last_name=user_payload.get("last_name", ""),
            language_code=user_payload.get("language_code", "en"),
            photo_url=user_payload.get("photo_url"),
        )
        if user.status != "active":
            raise AuthError("account is restricted")
        session_token = await self.create_session(user, user_agent=user_agent, ip=ip)
        await AuditService(self.session).log(
            "auth.login", actor_user_id=user.id, actor_type="user", ip_address=ip,
            metadata={"method": "telegram_webapp"},
        )
        return user, session_token

    async def create_session(
        self, user: User, *, user_agent: str = "", ip: str = "", ttl: int | None = None
    ) -> str:
        settings = get_settings()
        ttl = ttl or settings.session_ttl_seconds
        token = sign_session({"uid": user.id, "sub": user.telegram_id or 0}, ttl)
        self.session.add(
            AuthSession(
                user_id=user.id,
                token_hash=hash_api_key(token),
                kind="web",
                user_agent=user_agent[:256],
                ip_address=ip[:64],
                expires_at=datetime.utcnow() + timedelta(seconds=ttl),
            )
        )
        await self.session.flush()
        return token

    async def revoke_session(self, token: str) -> bool:
        record = await self.session.scalar(
            select(AuthSession).where(AuthSession.token_hash == hash_api_key(token))
        )
        if record is None:
            return False
        record.revoked_at = datetime.utcnow()
        await self.session.flush()
        return True

    async def revoke_all(self, user_id: int) -> int:
        result = await self.session.execute(
            delete(AuthSession).where(AuthSession.user_id == user_id)
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def issue_api_key(self, user: User, name: str, scopes: list[str]) -> tuple[str, Any]:
        from meiko.db.models import ApiKey

        plaintext, hashed = new_api_key()
        record = ApiKey(
            user_id=user.id,
            name=name[:120],
            prefix=plaintext.split("_")[0],
            key_hash=hashed,
            scopes=scopes,
        )
        self.session.add(record)
        await self.session.flush()
        return plaintext, record
