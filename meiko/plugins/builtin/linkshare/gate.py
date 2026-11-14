"""Access gating for protected channel links (LINK-SHAREBOT parity).

The rule set is deliberately data-only so every branch is testable offline:
``evaluate()`` takes *facts* (is the user a member of chat X, is this request
approved, has the post expired) and returns a verdict.  The plugin's job is
only to collect those facts from Telegram and act on the verdict.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: ChatMember statuses that count as "joined".
MEMBER_STATUSES = {"member", "administrator", "creator"}
#: Statuses that *cannot* be fixed by joining (they need an unban/unrestrict).
BLOCKED_STATUSES = {"kicked", "restricted"}


class Verdict(str, Enum):
    ALLOW = "allow"
    JOIN = "join"
    REQUEST = "request"
    PENDING = "pending"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"
    BLOCKED = "blocked"
    DISABLED = "disabled"


@dataclass
class RequiredChat:
    """A channel/group a viewer must join before the link is released."""

    chat_id: int
    title: str = ""
    invite_link: str = ""
    request_mode: bool = False  # join requests must be approved by an admin


@dataclass
class ProtectedLink:
    """One protected post → the link that is only released after the checks."""

    code: str
    url: str
    title: str = ""
    created_at: int = 0
    expires_at: int = 0
    max_claims: int = 0  # 0 = unlimited
    claims: int = 0
    required: list[RequiredChat] = field(default_factory=list)
    enabled: bool = True

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at

    @property
    def exhausted(self) -> bool:
        return bool(self.max_claims) and self.claims >= self.max_claims

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "url": self.url,
            "title": self.title,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "max_claims": self.max_claims,
            "claims": self.claims,
            "enabled": self.enabled,
            "required": [
                {
                    "chat_id": chat.chat_id,
                    "title": chat.title,
                    "invite_link": chat.invite_link,
                    "request_mode": chat.request_mode,
                }
                for chat in self.required
            ],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ProtectedLink | None":
        if not isinstance(data, dict) or not data.get("url"):
            return None
        return cls(
            code=str(data.get("code") or ""),
            url=str(data["url"]),
            title=str(data.get("title") or ""),
            created_at=int(data.get("created_at") or 0),
            expires_at=int(data.get("expires_at") or 0),
            max_claims=int(data.get("max_claims") or 0),
            claims=int(data.get("claims") or 0),
            enabled=bool(data.get("enabled", True)),
            required=[
                RequiredChat(
                    chat_id=int(row.get("chat_id") or 0),
                    title=str(row.get("title") or ""),
                    invite_link=str(row.get("invite_link") or ""),
                    request_mode=bool(row.get("request_mode")),
                )
                for row in data.get("required") or []
                if isinstance(row, dict)
            ],
        )


@dataclass
class Facts:
    """Everything the gate needs to know, gathered by the handler."""

    member_status: dict[int, str] = field(default_factory=dict)
    approved: bool = False
    pending: bool = False
    is_admin: bool = False
    already_claimed: bool = False


def missing_chats(link: ProtectedLink, facts: Facts) -> list[RequiredChat]:
    return [
        chat
        for chat in link.required
        if facts.member_status.get(chat.chat_id, "left") not in MEMBER_STATUSES
    ]


def evaluate(link: ProtectedLink, facts: Facts) -> tuple[Verdict, str]:
    """Decide whether to release the link, and say exactly why not."""
    if not link.enabled:
        return Verdict.DISABLED, "this link is turned off"
    if facts.is_admin:
        return Verdict.ALLOW, ""
    if link.expired:
        return Verdict.EXPIRED, "this link expired"
    if link.exhausted:
        return Verdict.EXHAUSTED, "this link reached its claim limit"

    blocked = [
        chat
        for chat in link.required
        if facts.member_status.get(chat.chat_id) in BLOCKED_STATUSES
    ]
    if blocked:
        return Verdict.BLOCKED, "you are restricted in " + ", ".join(chat.title or str(chat.chat_id) for chat in blocked)

    missing = missing_chats(link, facts)
    if missing:
        if facts.pending:
            return Verdict.PENDING, "your join request is waiting for approval"
        if any(chat.request_mode for chat in missing):
            return Verdict.REQUEST, "join requests are required for " + ", ".join(
                chat.title or str(chat.chat_id) for chat in missing
            )
        return Verdict.JOIN, "join " + ", ".join(chat.title or str(chat.chat_id) for chat in missing)

    if any(chat.request_mode for chat in link.required) and not facts.approved:
        return Verdict.PENDING if facts.pending else Verdict.REQUEST, "an admin must approve your request"
    return Verdict.ALLOW, ""


def token_for(link: ProtectedLink, user_id: int, secret: str) -> str:
    """Short, stable per-user token for the verify button (not a capability URL)."""
    digest = hmac.new(secret.encode(), f"{link.code}:{user_id}".encode(), hashlib.sha256).hexdigest()
    return digest[:12]


def code_for(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:8]


class LinkRegistry:
    """Durable registry of protected links + approved/pending viewers."""

    KEY = "protected"

    def __init__(self, store: Any, secret: str) -> None:
        if hasattr(store, "get_state") and not hasattr(store, "get"):
            from meiko.plugins.support import PluginState

            store = PluginState(store)
        self.store = store
        if not secret:
            from meiko.security.crypto import signing_secret

            secret = signing_secret("linkshare-gate")
        self.secret = secret
        self.links: dict[str, ProtectedLink] = {}
        self.approved: set[int] = set()
        self.pending: set[int] = set()

    async def load(self) -> "LinkRegistry":
        raw = await self.store.get(self.KEY, None) or {}
        if isinstance(raw, dict):
            for code, row in (raw.get("links") or {}).items():
                link = ProtectedLink.from_dict(row)
                if link is not None:
                    self.links[code] = link
            self.approved = {int(u) for u in raw.get("approved") or []}
            self.pending = {int(u) for u in raw.get("pending") or []}
        return self

    async def save(self) -> None:
        if self.store is None:
            return
        await self.store.set(
            self.KEY,
            {
                "links": {code: link.to_dict() for code, link in self.links.items()},
                "approved": sorted(self.approved),
                "pending": sorted(self.pending),
            },
        )

    # -- links ------------------------------------------------------------
    def add(self, link: ProtectedLink) -> ProtectedLink:
        self.links[link.code or code_for(link.url)] = link
        return link

    def get(self, key: str) -> ProtectedLink | None:
        if key in self.links:
            return self.links[key]
        for link in self.links.values():
            if link.url == key:
                return link
        return None

    def remove(self, key: str) -> bool:
        return self.links.pop(key, None) is not None

    def facts_for(self, link: ProtectedLink, user_id: int, statuses: dict[int, str] | None = None, *, is_admin: bool = False) -> Facts:
        return Facts(
            member_status=statuses or {},
            approved=user_id in self.approved,
            pending=user_id in self.pending,
            is_admin=is_admin,
        )

    # -- approvals --------------------------------------------------------
    def request(self, user_id: int) -> None:
        self.pending.add(user_id)

    def approve(self, user_id: int) -> bool:
        self.pending.discard(user_id)
        self.approved.add(user_id)
        return True

    def decline(self, user_id: int) -> bool:
        was = user_id in self.pending
        self.pending.discard(user_id)
        return was

    def claim(self, link: ProtectedLink) -> None:
        link.claims += 1


def verdict_copy(verdict: Verdict, detail: str, link: ProtectedLink) -> str:
    """User-facing text for every verdict (no bare enums in chat)."""
    if verdict is Verdict.ALLOW:
        return link.url
    if verdict is Verdict.JOIN:
        links = "\n".join(
            f"• <a href=\"{chat.invite_link}\">{chat.title or chat.chat_id}</a>"
            for chat in link.required
            if chat.invite_link
        )
        return f"Join first, then press verify.\n{links}".strip()
    return detail or "not available"


__all__ = [
    "BLOCKED_STATUSES",
    "Facts",
    "LinkRegistry",
    "MEMBER_STATUSES",
    "ProtectedLink",
    "RequiredChat",
    "Verdict",
    "code_for",
    "evaluate",
    "missing_chats",
    "token_for",
    "verdict_copy",
]
