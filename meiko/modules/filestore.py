"""Multi-user FileStore service: child bots, ownership, files, deep links.

Modelled on ``abhinai2244/Multi-FileStoreBot`` (main controller bot + worker
bots).  Meikoagent already *is* a multi-bot platform with a central worker
manager, so this module does not spawn its own bot runners: it registers a
FileStore "shop" for each bot a user owns, keeps that tenant's files, users,
admins and settings isolated, and issues deep links through the bot's own
``/start`` payload — the same signature scheme the vault already uses.

Security rules enforced here (not in the handlers):

* a bot token never reaches a response — only its masked form,
* ownership is checked on every read/write, including admin lists,
* deleting a shop does not delete the bot, only the shop's data,
* force-subscription requirements are stored, and checked by the runtime; the
  service exposes them so the API can show the truth and the bot can act on it.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from meiko.modules.links import sign_token, verify_token

VISIBILITIES = {"private", "link", "public"}


class ShopError(RuntimeError):
    """A FileStore rule was violated."""


def _mask(token: str) -> str:
    return f"{token[:6]}…{token[-4:]}" if len(token) > 12 else "…"


@dataclass
class ShopFile:
    id: str
    name: str
    size: int
    mime: str = "application/octet-stream"
    file_id: str = ""
    message_id: int = 0
    chat_id: int = 0
    caption: str = ""
    visibility: str = "private"
    tags: list[str] = field(default_factory=list)
    created_at: int = 0
    expires_at: int = 0
    downloads: int = 0
    batch: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__) | {"tags": list(self.tags)}

    @classmethod
    def from_dict(cls, data: Any) -> "ShopFile | None":
        if not isinstance(data, dict) or not data.get("id"):
            return None
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        payload.setdefault("name", "file")
        payload.setdefault("size", 0)
        return cls(**payload)


@dataclass
class Shop:
    """One FileStore bot tenant."""

    slug: str
    bot_id: int
    owner_id: int
    title: str = "My FileStore"
    created_at: int = 0
    active: bool = True
    settings: dict[str, Any] = field(default_factory=dict)
    admins: list[int] = field(default_factory=list)
    banned: list[int] = field(default_factory=list)
    files: dict[str, ShopFile] = field(default_factory=dict)
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    batches: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: dict[str, str] = field(default_factory=dict)
    force_sub: list[dict[str, Any]] = field(default_factory=list)
    shortener: dict[str, Any] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    # -- defaults ---------------------------------------------------------
    DEFAULT_MESSAGES = {
        "start": "Welcome to {shop}! Send me a file to store it.",
        "caption": "<code>{name}</code>\n{size}",
        "no_access": "You are not allowed to use this shop.",
        "force_sub": "Join our channels first, then press Verify.",
    }

    def message(self, key: str) -> str:
        return self.messages.get(key) or self.DEFAULT_MESSAGES.get(key, "")

    # -- access -----------------------------------------------------------
    def is_owner(self, user_id: int) -> bool:
        return user_id == self.owner_id

    def is_admin(self, user_id: int) -> bool:
        return self.is_owner(user_id) or user_id in self.admins

    def is_banned(self, user_id: int) -> bool:
        return user_id in self.banned

    def can_manage(self, user_id: int) -> bool:
        if self.is_banned(user_id):
            return False
        return self.is_admin(user_id)

    def visible_to(self, user_id: int) -> bool:
        if self.is_owner(user_id):
            return True
        return any(entry.visibility == "public" for entry in self.files.values())

    # -- serialisation ----------------------------------------------------
    def to_dict(self, *, include_files: bool = True) -> dict[str, Any]:
        document = {
            "slug": self.slug,
            "bot_id": self.bot_id,
            "owner_id": self.owner_id,
            "title": self.title,
            "created_at": self.created_at,
            "active": self.active,
            "settings": self.settings,
            "admins": self.admins,
            "banned": self.banned,
            "users": self.users,
            "batches": self.batches,
            "messages": self.messages,
            "force_sub": self.force_sub,
            "shortener": self.shortener,
            "counters": self.counters,
        }
        if include_files:
            document["files"] = {key: entry.to_dict() for key, entry in self.files.items()}
        return document

    @classmethod
    def from_dict(cls, data: Any) -> "Shop | None":
        if not isinstance(data, dict) or not data.get("bot_id"):
            return None
        shop = cls(
            slug=str(data.get("slug") or uuid.uuid4().hex[:8]),
            bot_id=int(data["bot_id"]),
            owner_id=int(data.get("owner_id") or 0),
            title=str(data.get("title") or "My FileStore"),
            created_at=int(data.get("created_at") or 0),
            active=bool(data.get("active", True)),
            settings=dict(data.get("settings") or {}),
            admins=[int(u) for u in data.get("admins") or []],
            banned=[int(u) for u in data.get("banned") or []],
            users=dict(data.get("users") or {}),
            batches=dict(data.get("batches") or {}),
            messages=dict(data.get("messages") or {}),
            force_sub=list(data.get("force_sub") or []),
            shortener=dict(data.get("shortener") or {}),
            counters={k: int(v) for k, v in (data.get("counters") or {}).items()},
        )
        for key, row in (data.get("files") or {}).items():
            entry = ShopFile.from_dict(row)
            if entry is not None:
                shop.files[key] = entry
        return shop

    # -- analytics --------------------------------------------------------
    def usage(self) -> dict[str, Any]:
        total = sum(entry.size for entry in self.files.values())
        return {
            "files": len(self.files),
            "bytes": total,
            "downloads": sum(entry.downloads for entry in self.files.values()),
            "users": len(self.users),
            "admins": len(self.admins),
            "banned": len(self.banned),
            "batches": len(self.batches),
            "links": self.counters.get("links", 0),
        }


class FileStoreService:
    """Shop registry persisted through the shared module store."""

    MODULE = "filestore"

    def __init__(self, store: Any) -> None:
        self.store = store

    # -- shops ------------------------------------------------------------
    def _document(self, scope: str) -> dict[str, Any]:
        return self.store.read(self.MODULE, scope)

    def _save(self, scope: str, shops: dict[str, Shop]) -> None:
        self.store.write(self.MODULE, scope, {"shops": {key: shop.to_dict() for key, shop in shops.items()}})

    def shops(self, scope: str) -> dict[str, Shop]:
        document = self._document(scope)
        shops: dict[str, Shop] = {}
        for key, row in (document.get("shops") or {}).items():
            shop = Shop.from_dict(row)
            if shop is not None:
                shops[key] = shop
        return shops

    def get(self, scope: str, slug: str) -> Shop:
        shop = self.shops(scope).get(slug)
        if shop is None:
            raise ShopError("no FileStore shop with that identifier")
        return shop

    def for_bot(self, scope: str, bot_id: int) -> Shop | None:
        return next((shop for shop in self.shops(scope).values() if shop.bot_id == bot_id), None)

    def save_shop(self, scope: str, shop: Shop) -> Shop:
        shops = self.shops(scope)
        shops[shop.slug] = shop
        self._save(scope, shops)
        return shop

    def register(
        self,
        scope: str,
        *,
        owner_id: int,
        bot_id: int,
        title: str = "",
        slug: str = "",
    ) -> Shop:
        shops = self.shops(scope)
        if any(shop.bot_id == bot_id for shop in shops.values()):
            raise ShopError("this bot already runs a FileStore")
        shop = Shop(
            slug=slug or uuid.uuid4().hex[:8],
            bot_id=bot_id,
            owner_id=owner_id,
            title=(title or f"FileStore {bot_id}")[:64],
            created_at=int(time.time()),
        )
        shop.users[str(owner_id)] = {"first_seen": int(time.time()), "role": "owner"}
        shops[shop.slug] = shop
        self._save(scope, shops)
        return shop

    def remove(self, scope: str, slug: str, *, owner_id: int) -> bool:
        shops = self.shops(scope)
        shop = shops.get(slug)
        if shop is None:
            return False
        if shop.owner_id != owner_id:
            raise ShopError("that FileStore belongs to someone else")
        shops.pop(slug, None)
        self._save(scope, shops)
        return True

    # -- ownership verification -------------------------------------------
    def verify_owner(self, scope: str, slug: str, *, user_id: int) -> dict[str, Any]:
        """Structured answer the API/commands can show without leaking anything."""
        try:
            shop = self.get(scope, slug)
        except ShopError:
            return {"verified": False, "reason": "unknown shop"}
        if shop.owner_id != user_id:
            return {"verified": False, "reason": "not the owner"}
        return {"verified": True, "bot_id": shop.bot_id, "title": shop.title}

    # -- files ------------------------------------------------------------
    def add_file(self, scope: str, slug: str, entry: ShopFile) -> ShopFile:
        shops = self.shops(scope)
        shop = shops.get(slug)
        if shop is None:
            raise ShopError("unknown shop")
        entry.created_at = entry.created_at or int(time.time())
        shop.files[entry.id] = entry
        self._save(scope, shops)
        return entry

    def remove_file(self, scope: str, slug: str, file_id: str, *, user_id: int) -> bool:
        shops = self.shops(scope)
        shop = shops.get(slug)
        if shop is None:
            raise ShopError("unknown shop")
        entry = shop.files.get(file_id)
        if entry is None:
            return False
        if not shop.is_admin(user_id):
            raise ShopError("only the owner or an admin can delete files")
        shop.files.pop(file_id, None)
        self._save(scope, shops)
        return True

    def list_files(
        self,
        scope: str,
        slug: str,
        *,
        user_id: int,
        query: str = "",
        page: int = 1,
        per_page: int = 10,
    ) -> tuple[list[ShopFile], int]:
        shop = self.get(scope, slug)
        rows = [
            entry
            for entry in shop.files.values()
            if shop.is_owner(user_id) or entry.visibility == "public" or entry.visibility == "link"
        ]
        needle = query.strip().lower()
        if needle:
            rows = [e for e in rows if needle in e.name.lower() or any(needle in t for t in e.tags)]
        rows.sort(key=lambda entry: entry.created_at, reverse=True)
        start = max(0, (max(1, page) - 1) * per_page)
        return rows[start : start + per_page], len(rows)

    def record_download(self, scope: str, slug: str, file_id: str) -> None:
        shops = self.shops(scope)
        shop = shops.get(slug)
        if shop is None:
            return
        entry = shop.files.get(file_id)
        if entry is None:
            return
        entry.downloads += 1
        self._save(scope, shops)

    # -- links ------------------------------------------------------------
    def issue_link(
        self,
        scope: str,
        slug: str,
        file_id: str,
        *,
        secret: str,
        ttl_days: int = 30,
        max_uses: int = 0,
        batch: str = "",
    ) -> dict[str, Any]:
        shop = self.get(scope, slug)
        entry = shop.files.get(file_id)
        if entry is None:
            raise ShopError("unknown file")
        expires = int(time.time() + ttl_days * 86400) if ttl_days else 0
        payload = {"shop": slug, "bot": shop.bot_id, "file": file_id, "exp": expires, "uses": max_uses}
        token = sign_token(payload, secret)
        shop.counters["links"] = shop.counters.get("links", 0) + 1
        if batch:
            shop.batches.setdefault(batch, {"created_at": int(time.time()), "files": []})
            shop.batches[batch]["files"].append(file_id)
        self.save_shop(scope, shop)
        return {
            "token": token,
            "payload": payload,
            "start": f"https://t.me/{{bot}}?start=fs_{token}",
            "file": entry.name,
            "expires_at": expires,
            "max_uses": max_uses,
        }

    def redeem(self, scope: str, token: str, *, secret: str) -> tuple[ShopFile | None, str]:
        payload = verify_token(token, secret)
        if payload is None:
            return None, "this link is invalid or was tampered with"
        shops = self.shops(scope)
        shop = shops.get(str(payload.get("shop") or ""))
        if shop is None:
            return None, "this shop no longer exists"
        entry = shop.files.get(str(payload.get("file") or ""))
        if entry is None:
            return None, "the file was removed"
        expires = int(payload.get("exp") or 0)
        if expires and time.time() > expires:
            return None, "this link expired"
        if entry.expires_at and time.time() > entry.expires_at:
            return None, "the file expired"
        max_uses = int(payload.get("uses") or 0)
        if max_uses and entry.downloads >= max_uses:
            return None, "this link reached its use limit"
        self.record_download(scope, shop.slug, entry.id)
        return entry, ""

    # -- administration ---------------------------------------------------
    def set_setting(self, scope: str, slug: str, key: str, value: Any, *, user_id: int) -> Shop:
        shop = self.get(scope, slug)
        if not shop.can_manage(user_id):
            raise ShopError("only the owner or an admin can change settings")
        shop.settings[str(key)] = value
        return self.save_shop(scope, shop)

    def set_message(self, scope: str, slug: str, key: str, value: str, *, user_id: int) -> Shop:
        shop = self.get(scope, slug)
        if not shop.can_manage(user_id):
            raise ShopError("only the owner or an admin can change messages")
        shop.messages[str(key)] = str(value)[:1024]
        return self.save_shop(scope, shop)

    def add_admin(self, scope: str, slug: str, user_id: int, *, actor_id: int) -> Shop:
        shop = self.get(scope, slug)
        if not shop.is_owner(actor_id):
            raise ShopError("only the owner can add admins")
        if user_id not in shop.admins and user_id != shop.owner_id:
            shop.admins.append(int(user_id))
        shop.users.setdefault(str(user_id), {"first_seen": int(time.time()), "role": "admin"})
        return self.save_shop(scope, shop)

    def remove_admin(self, scope: str, slug: str, user_id: int, *, actor_id: int) -> Shop:
        shop = self.get(scope, slug)
        if not shop.is_owner(actor_id):
            raise ShopError("only the owner can remove admins")
        shop.admins = [entry for entry in shop.admins if entry != int(user_id)]
        return self.save_shop(scope, shop)

    def ban(self, scope: str, slug: str, user_id: int, *, actor_id: int, banned: bool = True) -> Shop:
        shop = self.get(scope, slug)
        if not shop.can_manage(actor_id):
            raise ShopError("only the owner or an admin can ban users")
        if user_id == shop.owner_id:
            raise ShopError("the owner cannot be banned")
        if banned and user_id not in shop.banned:
            shop.banned.append(int(user_id))
        if not banned:
            shop.banned = [entry for entry in shop.banned if entry != int(user_id)]
        return self.save_shop(scope, shop)

    def force_sub(self, scope: str, slug: str, chat_id: int, *, title: str = "", actor_id: int) -> Shop:
        shop = self.get(scope, slug)
        if not shop.can_manage(actor_id):
            raise ShopError("only the owner or an admin can set force-subscription")
        shop.force_sub.append({"chat_id": int(chat_id), "title": title, "added_at": int(time.time())})
        return self.save_shop(scope, shop)

    def clear_force_sub(self, scope: str, slug: str, *, actor_id: int) -> Shop:
        shop = self.get(scope, slug)
        if not shop.can_manage(actor_id):
            raise ShopError("only the owner or an admin can clear force-subscription")
        shop.force_sub = []
        return self.save_shop(scope, shop)

    def shortener(
        self,
        scope: str,
        slug: str,
        *,
        actor_id: int,
        base_url: str = "",
        api_key: str = "",
    ) -> Shop:
        shop = self.get(scope, slug)
        if not shop.can_manage(actor_id):
            raise ShopError("only the owner or an admin can configure a shortener")
        shop.shortener = {
            "base_url": base_url.strip().rstrip("/"),
            "api_key": _mask(api_key) if api_key else "",
            "has_key": bool(api_key or shop.shortener.get("has_key")),
            "updated_at": int(time.time()),
        }
        return self.save_shop(scope, shop)

    def export(self, scope: str, slug: str, *, user_id: int) -> dict[str, Any]:
        """Portable shop export — never includes credentials."""
        shop = self.get(scope, slug)
        if not shop.is_owner(user_id):
            raise ShopError("only the owner can export a FileStore")
        document = shop.to_dict()
        document.pop("shortener", None)
        return document


__all__ = ["FileStoreService", "Shop", "ShopError", "ShopFile", "VISIBILITIES"]
