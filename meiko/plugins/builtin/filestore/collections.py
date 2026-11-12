"""Multi-user vault model: collections, per-user quotas, encodings, TTLs.

Modelled on ``abhinai2244/Multi-FileStoreBot``: several people store files in
one bot, each with their own quota, folders and private/public visibility.
Everything here is a pure model over an index, so quota accounting and
visibility rules are testable without Telegram.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

MEGABYTE = 1024 * 1024
VISIBILITIES = {"private", "link", "public"}


class VaultError(RuntimeError):
    """A vault rule was violated (quota, visibility, duplicate name)."""


@dataclass
class StoredFile:
    id: str
    name: str
    size: int
    owner_id: int
    file_id: str = ""
    mime: str = "application/octet-stream"
    collection: str = "default"
    visibility: str = "private"
    created_at: int = 0
    expires_at: int = 0
    downloads: int = 0
    tags: list[str] = field(default_factory=list)
    message_id: int = 0
    chat_id: int = 0

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "owner_id": self.owner_id,
            "file_id": self.file_id,
            "mime": self.mime,
            "collection": self.collection,
            "visibility": self.visibility,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "downloads": self.downloads,
            "tags": list(self.tags),
            "message_id": self.message_id,
            "chat_id": self.chat_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "StoredFile | None":
        if not isinstance(data, dict) or not data.get("id"):
            return None
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or "file"),
            size=int(data.get("size") or 0),
            owner_id=int(data.get("owner_id") or 0),
            file_id=str(data.get("file_id") or ""),
            mime=str(data.get("mime") or "application/octet-stream"),
            collection=str(data.get("collection") or "default"),
            visibility=str(data.get("visibility") or "private"),
            created_at=int(data.get("created_at") or 0),
            expires_at=int(data.get("expires_at") or 0),
            downloads=int(data.get("downloads") or 0),
            tags=[str(tag) for tag in data.get("tags") or []],
            message_id=int(data.get("message_id") or 0),
            chat_id=int(data.get("chat_id") or 0),
        )

    def visible_to(self, user_id: int) -> bool:
        if self.owner_id == user_id:
            return True
        return self.visibility == "public"


@dataclass
class Collection:
    name: str
    owner_id: int
    created_at: int = 0
    public: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "owner_id": self.owner_id,
            "created_at": self.created_at,
            "public": self.public,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Collection | None":
        if not isinstance(data, dict) or not data.get("name"):
            return None
        return cls(
            name=str(data["name"]),
            owner_id=int(data.get("owner_id") or 0),
            created_at=int(data.get("created_at") or 0),
            public=bool(data.get("public")),
        )


class Vault:
    """Files + collections + quotas for one bot scope."""

    def __init__(
        self,
        *,
        global_quota: int = 2048 * MEGABYTE,
        per_user_quota: int = 256 * MEGABYTE,
        max_file_bytes: int = 20 * MEGABYTE,
        max_files: int = 5000,
    ) -> None:
        self.files: dict[str, StoredFile] = {}
        self.collections: dict[str, Collection] = {}
        self.global_quota = global_quota
        self.per_user_quota = per_user_quota
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "files": {key: entry.to_dict() for key, entry in self.files.items()},
            "collections": {key: col.to_dict() for key, col in self.collections.items()},
        }

    @classmethod
    def from_dict(cls, data: Any, **kwargs: Any) -> "Vault":
        vault = cls(**kwargs)
        if isinstance(data, dict):
            for key, row in (data.get("files") or {}).items():
                entry = StoredFile.from_dict(row)
                if entry is not None:
                    vault.files[key] = entry
            for key, row in (data.get("collections") or {}).items():
                col = Collection.from_dict(row)
                if col is not None:
                    vault.collections[key] = col
        return vault

    # -- accounting -------------------------------------------------------
    def used(self, owner_id: int | None = None) -> int:
        if owner_id is None:
            return sum(entry.size for entry in self.files.values())
        return sum(entry.size for entry in self.files.values() if entry.owner_id == owner_id)

    def count(self, owner_id: int | None = None) -> int:
        if owner_id is None:
            return len(self.files)
        return sum(1 for entry in self.files.values() if entry.owner_id == owner_id)

    def check_quota(self, owner_id: int, size: int) -> None:
        """Raise :class:`VaultError` when a file cannot be accepted."""
        if size > self.max_file_bytes:
            raise VaultError(
                f"file is {size / MEGABYTE:.1f} MB; the limit is {self.max_file_bytes / MEGABYTE:.0f} MB"
            )
        if len(self.files) >= self.max_files:
            raise VaultError("the vault is full")
        if self.used(owner_id) + size > self.per_user_quota:
            raise VaultError(
                f"your quota is {self.per_user_quota / MEGABYTE:.0f} MB "
                f"({self.used(owner_id) / MEGABYTE:.0f} MB used)"
            )
        if self.used() + size > self.global_quota:
            raise VaultError("the shared vault quota is exhausted")

    # -- mutations --------------------------------------------------------
    def add(self, entry: StoredFile) -> StoredFile:
        self.check_quota(entry.owner_id, entry.size)
        self.files[entry.id] = entry
        return entry

    def remove(self, file_id: str, *, owner_id: int | None = None) -> StoredFile | None:
        entry = self.files.get(file_id)
        if entry is None:
            return None
        if owner_id is not None and entry.owner_id != owner_id:
            return None
        return self.files.pop(file_id)

    def get(self, file_id_or_name: str, *, user_id: int = 0) -> StoredFile | None:
        entry = self.files.get(file_id_or_name)
        if entry is None:
            matches = [f for f in self.files.values() if f.name.lower() == file_id_or_name.lower()]
            entry = matches[0] if matches else None
        if entry is None or not entry.visible_to(user_id):
            return None
        return entry

    def list_for(
        self,
        user_id: int,
        *,
        collection: str | None = None,
        query: str = "",
        page: int = 1,
        per_page: int = 8,
    ) -> tuple[list[StoredFile], int]:
        needle = (query or "").strip().lower()
        rows = [entry for entry in self.files.values() if entry.visible_to(user_id)]
        if collection:
            rows = [entry for entry in rows if entry.collection == collection.lower()]
        if needle:
            rows = [
                entry
                for entry in rows
                if needle in entry.name.lower() or any(needle in tag for tag in entry.tags)
            ]
        rows.sort(key=lambda entry: entry.created_at, reverse=True)
        total = len(rows)
        start = max(0, (max(1, page) - 1) * per_page)
        return rows[start : start + per_page], total

    def expiry_sweep(self, now: int | None = None) -> list[StoredFile]:
        current = now or int(time.time())
        gone = [entry for entry in self.files.values() if entry.expires_at and entry.expires_at < current]
        for entry in gone:
            self.files.pop(entry.id, None)
        return gone

    # -- collections ------------------------------------------------------
    def create_collection(self, name: str, owner_id: int, *, public: bool = False) -> Collection:
        key = (name or "").strip().lower()[:32]
        if not key:
            raise VaultError("collection needs a name")
        if key in self.collections and self.collections[key].owner_id != owner_id:
            raise VaultError("that collection belongs to someone else")
        collection = Collection(key, owner_id, created_at=int(time.time()), public=public)
        self.collections[key] = collection
        return collection

    def collections_for(self, user_id: int) -> list[Collection]:
        return [col for col in self.collections.values() if col.owner_id == user_id or col.public]

    def tags(self) -> list[str]:
        seen: set[str] = set()
        for entry in self.files.values():
            seen.update(entry.tags)
        return sorted(seen)


def encoding_choices() -> dict[str, str]:
    """Deliver-as hints (Filestore parity: document / video / audio / stream)."""
    return {
        "document": "send as a document (keeps the filename)",
        "video": "send as playable video",
        "audio": "send as playable audio",
        "stream": "hand over a share link instead of the file",
    }


__all__ = [
    "Collection",
    "MEGABYTE",
    "StoredFile",
    "VISIBILITIES",
    "Vault",
    "VaultError",
    "encoding_choices",
]
