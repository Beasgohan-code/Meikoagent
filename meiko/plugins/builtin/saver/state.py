"""Persistent saver settings and the saved-post index.

Everything lives in plugin state (``SystemSetting``) so it survives restarts and
is scoped per bot/chat.  No filesystem, no aiogram: just validation + JSON-safe
serialisation, which keeps it trivially testable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .captions import DEFAULT_CAPTION, WordRules

STATE_KEY = "saver:config"
INDEX_KEY = "index:saved"
SEND_MODES = ("copy", "document", "video")
MAX_BATCH_CHOICES = (1, 5, 10, 20, 50)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SaverConfig:
    """All knobs the owner can change from chat."""

    caption_template: str = DEFAULT_CAPTION
    use_caption: bool = False
    delete_words: list[str] = field(default_factory=list)
    replace_words: list[str] = field(default_factory=list)
    send_mode: str = "copy"
    dump_chat: int | str | None = None
    thumbnail_file_id: str | None = None
    max_batch: int = 20
    delay_seconds: float = 0.4
    skip_duplicates: bool = True
    notify_each: bool = False
    updated_at: str = ""

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "SaverConfig":
        if not isinstance(data, dict):
            return cls()
        config = cls()
        for name, value in data.items():
            if not hasattr(config, name):
                continue
            if name == "delete_words" and isinstance(value, list):
                config.delete_words = [str(item) for item in value][:50]
            elif name == "replace_words" and isinstance(value, list):
                config.replace_words = [str(item) for item in value][:50]
            elif name == "max_batch":
                config.max_batch = min(50, max(1, int(value or 20)))
            elif name == "delay_seconds":
                config.delay_seconds = min(5.0, max(0.0, float(value or 0)))
            elif name == "send_mode":
                config.send_mode = str(value) if value in SEND_MODES else "copy"
            elif name in {"use_caption", "skip_duplicates", "notify_each"}:
                setattr(config, name, bool(value))
            elif name in {"dump_chat", "thumbnail_file_id", "caption_template", "updated_at"}:
                setattr(config, name, value)
        return config

    # -- derived ---------------------------------------------------------
    @property
    def rules(self) -> WordRules:
        return WordRules(delete=list(self.delete_words), replace=list(self.replace_words))

    @property
    def needs_download(self) -> bool:
        """Custom captions, word rules or a thumbnail force a re-upload."""
        return bool(self.use_caption or self.thumbnail_file_id or not self.rules.empty)

    def touch(self) -> None:
        self.updated_at = _now()

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("Caption", (self.caption_template or "")[:60] + ("…" if len(self.caption_template or "") > 60 else "")),
            ("Apply caption", "yes" if self.use_caption else "no"),
            ("Word rules", self.rules.describe()),
            ("Send mode", self.send_mode),
            ("Dump chat", str(self.dump_chat) if self.dump_chat else "current chat"),
            ("Thumbnail", "set" if self.thumbnail_file_id else "none"),
            ("Batch size", str(self.max_batch)),
            ("Skip duplicates", "yes" if self.skip_duplicates else "no"),
        ]


@dataclass
class SavedPost:
    chat: str
    message_id: int
    unique_id: str = ""
    filename: str = ""
    size: int = 0
    kind: str = "file"
    saved_at: str = ""
    duplicate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SavedPost":
        return cls(
            chat=str(data.get("chat", "")),
            message_id=int(data.get("message_id", 0)),
            unique_id=str(data.get("unique_id", "")),
            filename=str(data.get("filename", "")),
            size=int(data.get("size", 0)),
            kind=str(data.get("kind", "file")),
            saved_at=str(data.get("saved_at", "")),
            duplicate=bool(data.get("duplicate", False)),
        )


class SavedIndex:
    """Dedup + listing for everything the saver has stored."""

    def __init__(self, entries: list[dict[str, Any]] | None = None, limit: int = 2000) -> None:
        self._entries: list[SavedPost] = []
        self._seen: set[str] = set()
        self.limit = limit
        for entry in entries or []:
            post = SavedPost.from_dict(entry)
            self._entries.append(post)
            if post.unique_id:
                self._seen.add(post.unique_id)

    # -- queries ---------------------------------------------------------
    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[SavedPost]:
        return list(self._entries)

    def is_duplicate(self, unique_id: str) -> bool:
        return bool(unique_id) and unique_id in self._seen

    def add(self, post: SavedPost) -> bool:
        """Returns False when the post was already indexed."""
        if post.unique_id and post.unique_id in self._seen:
            return False
        post.duplicate = False
        post.saved_at = post.saved_at or _now()
        self._entries.insert(0, post)
        if post.unique_id:
            self._seen.add(post.unique_id)
        del self._entries[self.limit :]
        return True

    def recent(self, limit: int = 10) -> list[SavedPost]:
        return self._entries[:limit]

    def stats(self) -> dict[str, int | float]:
        total = sum(post.size for post in self._entries)
        return {
            "count": len(self._entries),
            "bytes": total,
            "megabytes": round(total / (1024 * 1024), 2),
        }

    def to_list(self) -> list[dict[str, Any]]:
        return [post.to_dict() for post in self._entries]


def apply_word_rules(text: str, config: SaverConfig) -> str:
    return config.rules.apply(text)


def validate_caption(template: str) -> tuple[bool, str]:
    """Captions must be short and contain at least one placeholder."""
    if not template.strip():
        return False, "caption is empty"
    if len(template) > 1000:
        return False, "caption is limited to 1000 characters"
    if "{" not in template:
        return False, "add at least one placeholder, e.g. {filename} or {size}"
    return True, "ok"


__all__ = [
    "INDEX_KEY",
    "MAX_BATCH_CHOICES",
    "SEND_MODES",
    "STATE_KEY",
    "SavedIndex",
    "SavedPost",
    "SaverConfig",
    "apply_word_rules",
    "validate_caption",
]
