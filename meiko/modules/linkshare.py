"""Linkshare service: share history, batches, access controls, analytics.

Extends the protected-link gate with the management surface the dashboard needs:
per-bot link registries, batches, revocation, expiry and *honest* analytics.
Telegram does not report "link opened" for Bot API bots, so the numbers here are
what we can actually observe: claims (a viewer passed the gate), refusals (with
the verdict that caused them) and per-file download counters.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from meiko.modules.links import short_code, sign_token, verify_token
from meiko.modules.urls import UrlRejected, check as check_url


class LinkError(RuntimeError):
    """A link rule was violated."""


@dataclass
class ShareLink:
    code: str
    url: str
    owner_id: int
    bot_id: int = 0
    title: str = ""
    created_at: int = 0
    expires_at: int = 0
    max_claims: int = 0
    claims: int = 0
    refusals: int = 0
    revoked: bool = False
    batch: str = ""
    required_chats: list[int] = field(default_factory=list)
    verdicts: dict[str, int] = field(default_factory=dict)
    file_name: str = ""

    @property
    def state(self) -> str:
        if self.revoked:
            return "revoked"
        if self.expires_at and time.time() > self.expires_at:
            return "expired"
        if self.max_claims and self.claims >= self.max_claims:
            return "exhausted"
        return "active"

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.__dict__)
        payload["required_chats"] = list(self.required_chats)
        payload["verdicts"] = dict(self.verdicts)
        payload["state"] = self.state
        return payload

    @classmethod
    def from_dict(cls, data: Any) -> "ShareLink | None":
        if not isinstance(data, dict) or not data.get("code"):
            return None
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        payload.setdefault("url", "")
        payload.setdefault("owner_id", 0)
        return cls(**payload)


class LinkshareService:
    """Durable link registry with batches and per-bot isolation."""

    MODULE = "linkshare"

    def __init__(self, store: Any, secret: str = "meiko-linkshare") -> None:
        self.store = store
        self.secret = secret

    def _read(self, scope: str) -> dict[str, Any]:
        document = self.store.read(self.MODULE, scope)
        document.setdefault("links", {})
        document.setdefault("batches", {})
        return document

    def _write(self, scope: str, document: dict[str, Any]) -> None:
        self.store.write(self.MODULE, scope, document)

    # -- creation ---------------------------------------------------------
    def create(
        self,
        scope: str,
        *,
        url: str,
        owner_id: int,
        title: str = "",
        ttl_hours: int = 24,
        max_claims: int = 0,
        bot_id: int = 0,
        required_chats: list[int] | None = None,
        file_name: str = "",
    ) -> ShareLink:
        if not url:
            raise LinkError("a link needs a URL")
        try:
            url = check_url(url)
        except UrlRejected as exc:
            raise LinkError(str(exc)) from exc
        document = self._read(scope)
        code = short_code(8)
        while code in document["links"]:
            code = short_code(8)
        link = ShareLink(
            code=code,
            url=url,
            owner_id=owner_id,
            bot_id=bot_id,
            title=title[:80],
            created_at=int(time.time()),
            expires_at=int(time.time() + ttl_hours * 3600) if ttl_hours else 0,
            max_claims=max_claims,
            required_chats=list(required_chats or []),
            file_name=file_name,
        )
        document["links"][code] = link.to_dict()
        self._write(scope, document)
        return link

    def create_batch(
        self,
        scope: str,
        *,
        urls: list[str],
        owner_id: int,
        ttl_hours: int = 24,
        max_claims: int = 0,
        bot_id: int = 0,
    ) -> tuple[str, list[ShareLink]]:
        # validate the whole batch first: creating half a batch and then raising
        # would leave links behind that the caller was told were refused
        for candidate in urls:
            try:
                check_url(candidate)
            except UrlRejected as exc:
                raise LinkError(str(exc)) from exc
        batch = uuid.uuid4().hex[:8]
        links = [
            self.create(
                scope, url=url, owner_id=owner_id, ttl_hours=ttl_hours,
                max_claims=max_claims, bot_id=bot_id, title=f"batch {batch}",
            )
            for url in urls
        ]
        document = self._read(scope)
        for link in links:
            row = document["links"][link.code]
            row["batch"] = batch
        document["batches"][batch] = {
            "created_at": int(time.time()),
            "owner_id": owner_id,
            "codes": [link.code for link in links],
        }
        self._write(scope, document)
        return batch, links

    # -- reads ------------------------------------------------------------
    def get(self, scope: str, code: str) -> ShareLink | None:
        row = self._read(scope)["links"].get(code)
        return ShareLink.from_dict(row) if row else None

    def list_links(
        self,
        scope: str,
        *,
        owner_id: int | None = None,
        state: str = "",
        batch: str = "",
        limit: int = 100,
    ) -> list[ShareLink]:
        rows = []
        for row in self._read(scope)["links"].values():
            link = ShareLink.from_dict(row)
            if link is None:
                continue
            if owner_id is not None and link.owner_id != owner_id:
                continue
            if state and link.state != state:
                continue
            if batch and link.batch != batch:
                continue
            rows.append(link)
        rows.sort(key=lambda link: link.created_at, reverse=True)
        return rows[:limit]

    def batches(self, scope: str) -> dict[str, Any]:
        return dict(self._read(scope)["batches"])

    # -- lifecycle --------------------------------------------------------
    def revoke(self, scope: str, code: str, *, owner_id: int | None = None) -> bool:
        document = self._read(scope)
        row = document["links"].get(code)
        if row is None:
            return False
        if owner_id is not None and int(row.get("owner_id") or 0) != owner_id:
            raise LinkError("that link belongs to someone else")
        row["revoked"] = True
        self._write(scope, document)
        return True

    def revoke_batch(self, scope: str, batch: str, *, owner_id: int) -> int:
        document = self._read(scope)
        info = document["batches"].get(batch)
        if info is None:
            raise LinkError("unknown batch")
        if int(info.get("owner_id") or 0) != owner_id:
            raise LinkError("that batch belongs to someone else")
        revoked = 0
        for code in info.get("codes", []):
            row = document["links"].get(code)
            if row and not row.get("revoked"):
                row["revoked"] = True
                revoked += 1
        self._write(scope, document)
        return revoked

    def claim(self, scope: str, code: str, *, verdict: str = "allow") -> tuple[ShareLink | None, str]:
        """Record an access attempt. Refusals are counted, never silently dropped."""
        document = self._read(scope)
        row = document["links"].get(code)
        if row is None:
            return None, "unknown link"
        link = ShareLink.from_dict(row)
        assert link is not None
        state = link.state
        if state != "active":
            row["refusals"] = int(row.get("refusals") or 0) + 1
            verdicts = dict(row.get("verdicts") or {})
            verdicts[state] = verdicts.get(state, 0) + 1
            row["verdicts"] = verdicts
            self._write(scope, document)
            return None, f"this link is {state}"
        row["claims"] = int(row.get("claims") or 0) + 1
        verdicts = dict(row.get("verdicts") or {})
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        row["verdicts"] = verdicts
        self._write(scope, document)
        return ShareLink.from_dict(row), ""

    # -- shortener --------------------------------------------------------
    def short_token(self, scope: str, code: str) -> str:
        """A signed token for an optional external shortener redirect."""
        link = self.get(scope, code)
        if link is None:
            raise LinkError("unknown link")
        return sign_token({"code": code, "exp": link.expires_at}, self.secret)

    def resolve_token(self, token: str) -> dict[str, Any] | None:
        return verify_token(token, self.secret)

    # -- analytics --------------------------------------------------------
    def analytics(self, scope: str) -> dict[str, Any]:
        links = self.list_links(scope, limit=10_000)
        by_state: dict[str, int] = {}
        for link in links:
            by_state[link.state] = by_state.get(link.state, 0) + 1
        verdicts: dict[str, int] = {}
        for link in links:
            for name, count in link.verdicts.items():
                verdicts[name] = verdicts.get(name, 0) + count
        return {
            "total": len(links),
            "active": by_state.get("active", 0),
            "expired": by_state.get("expired", 0),
            "revoked": by_state.get("revoked", 0),
            "exhausted": by_state.get("exhausted", 0),
            "claims": sum(link.claims for link in links),
            "refusals": sum(link.refusals for link in links),
            "batches": len(self.batches(scope)),
            "verdicts": verdicts,
            "note": (
                "claims are gate passes observed by this bot; Telegram does not report "
                "whether an external link was opened"
            ),
        }


__all__ = ["LinkError", "LinkshareService", "ShareLink"]
