"""Share-link generation and verification (Multi-FileStoreBot parity).

A "share link" here is a **signed capability URL**: ``/start s_<token>`` with an
HMAC over the file id, owner and expiry.  No account is created, nothing is
public, and a revoked link is revoked for everyone because verification is
stateless and the token is also checked against the vault index.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any

ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no look-alikes
DEFAULT_TTL = 7 * 86400


def short_code(length: int = 8) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def _sign(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:9]).decode().rstrip("=")


@dataclass
class ShareLink:
    token: str
    file_id: str
    owner_id: int
    expires_at: int
    uses: int = 0
    max_uses: int = 0  # 0 = unlimited
    created_at: int = 0

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at

    @property
    def exhausted(self) -> bool:
        return bool(self.max_uses) and self.uses >= self.max_uses

    def usable(self) -> tuple[bool, str]:
        if self.expired:
            return False, "this link expired"
        if self.exhausted:
            return False, "this link reached its limit"
        return True, ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "file_id": self.file_id,
            "owner_id": self.owner_id,
            "expires_at": self.expires_at,
            "uses": self.uses,
            "max_uses": self.max_uses,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ShareLink | None":
        if not isinstance(data, dict) or not data.get("token"):
            return None
        return cls(
            token=str(data["token"]),
            file_id=str(data.get("file_id") or ""),
            owner_id=int(data.get("owner_id") or 0),
            expires_at=int(data.get("expires_at") or 0),
            uses=int(data.get("uses") or 0),
            max_uses=int(data.get("max_uses") or 0),
            created_at=int(data.get("created_at") or 0),
        )


def issue(file_id: str, owner_id: int, secret: str, *, ttl_days: int = 7, max_uses: int = 0) -> ShareLink:
    """Mint a signed link. The token embeds file, owner and expiry, so a random
    guess cannot be turned into a valid link without the secret."""
    expires = int(time.time() + max(1, ttl_days) * 86400) if ttl_days else 0
    payload = f"{file_id}:{owner_id}:{expires}:{max_uses}"
    token = f"{short_code(4)}{expires and expires % 100000 or 0:05d}{_sign(secret, payload)}"
    return ShareLink(
        token=token,
        file_id=file_id,
        owner_id=owner_id,
        expires_at=expires,
        max_uses=max_uses,
        created_at=int(time.time()),
    )


def verify(link: ShareLink, token: str, secret: str) -> tuple[bool, str]:
    """Check a token against a stored link: signature, expiry and use count."""
    if not token or not link:
        return False, "unknown link"
    if not hmac.compare_digest(link.token, token):
        return False, "this link is not valid"
    payload = f"{link.file_id}:{link.owner_id}:{link.expires_at}:{link.max_uses}"
    returned = token[9:]
    if not hmac.compare_digest(_sign(secret, payload), returned):
        return False, "this link was tampered with"
    return link.usable()


def start_deep_link(bot_username: str, token: str) -> str:
    username = (bot_username or "bot").lstrip("@")
    return f"https://t.me/{username}?start=s_{token}"


def parse_start_payload(payload: str) -> str:
    """``s_abcdef`` → ``abcdef`` (``""`` for anything else)."""
    raw = (payload or "").strip()
    return raw[2:] if raw.startswith("s_") else ""


class LinkBook:
    """Durable link book: one token per file, revocable, with use counters."""

    KEY = "shares"

    def __init__(self, store: Any, secret: str) -> None:
        # accept a PluginState-like store *or* a PluginContext; PluginState is
        # the right abstraction for persistence, a context would bypass the
        # plugin's own namespace.
        if hasattr(store, "get_state") and not hasattr(store, "get"):
            from meiko.plugins.support import PluginState

            store = PluginState(store)
        self.store = store
        self.secret = secret or "meiko-dev-secret"
        self.links: dict[str, ShareLink] = {}

    async def load(self) -> "LinkBook":
        raw = await self.store.get(self.KEY, None) or {}
        if isinstance(raw, dict):
            for token, row in raw.items():
                link = ShareLink.from_dict(row)
                if link is not None:
                    self.links[token] = link
        return self

    async def save(self) -> None:
        if self.store is None:
            return
        await self.store.set(self.KEY, {token: link.to_dict() for token, link in self.links.items()})

    def by_file(self, file_id: str) -> list[ShareLink]:
        return [link for link in self.links.values() if link.file_id == file_id]

    def add(self, link: ShareLink) -> ShareLink:
        self.links[link.token] = link
        return link

    def revoke(self, token: str) -> bool:
        return self.links.pop(token, None) is not None

    def revoke_file(self, file_id: str) -> int:
        tokens = [token for token, link in self.links.items() if link.file_id == file_id]
        for token in tokens:
            self.links.pop(token, None)
        return len(tokens)

    def resolve(self, token: str) -> tuple[ShareLink | None, str]:
        link = self.links.get(token)
        if link is None:
            return None, "unknown link"
        ok, reason = verify(link, token, self.secret)
        return (link if ok else None), reason

    def consume(self, link: ShareLink) -> None:
        link.uses += 1


__all__ = [
    "ALPHABET",
    "DEFAULT_TTL",
    "LinkBook",
    "ShareLink",
    "issue",
    "parse_start_payload",
    "short_code",
    "start_deep_link",
    "verify",
]
