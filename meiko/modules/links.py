"""Signed tokens shared by FileStore deep links and Linkshare URLs.

Both modules need "a link that cannot be forged, can expire and can carry a use
cap".  One implementation: HMAC-SHA256 over a canonical JSON payload, base64url
encoded, with the signature *inside* the token so editing any field invalidates
it.  This is deliberately not a JWT: no algorithm negotiation, no ``alg: none``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def short_code(length: int = 8) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def sign_token(payload: dict[str, Any], secret: str) -> str:
    body = _b64(canonical(payload))
    signature = hmac.new((secret or "").encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def verify_token(token: str, secret: str) -> dict[str, Any] | None:
    """Return the payload, or ``None`` for anything malformed or forged."""
    if not token or token.count(".") != 1:
        return None
    body, signature = token.split(".", 1)
    expected = hmac.new((secret or "").encode(), body.encode(), hashlib.sha256).digest()
    try:
        provided = _unb64(signature)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected, provided):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


__all__ = ["ALPHABET", "canonical", "short_code", "sign_token", "verify_token"]
