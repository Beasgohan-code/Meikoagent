"""Caption templating and word rules for the saver.

Videl1 lets an admin set a caption with ``{filename}`` / ``{size}`` placeholders
and a list of words to delete or replace.  This module does that, plus safe
escaping (so a filename containing ``<`` cannot break the HTML), size/date
helpers and ordered rules where replacement runs before deletion.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_CAPTION = "{filename}\n\n<code>{size}</code>"
PLACEHOLDER_RE = re.compile(r"\{([a-z_]{2,16})(?::([^{}]{1,32}))?\}")
LIMIT = 1024


def human_size(size: int | float) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def duration(seconds: int | float | None) -> str:
    total = int(seconds or 0)
    if total <= 0:
        return ""
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


@dataclass
class CaptionContext:
    filename: str = ""
    size: int = 0
    title: str = ""
    source: str = ""
    chat_title: str = ""
    message_id: int = 0
    date: datetime | None = None
    duration_seconds: int = 0
    extra: dict[str, str] = field(default_factory=dict)

    def values(self) -> dict[str, str]:
        stamp = self.date or datetime.now(timezone.utc)
        return {
            "filename": self.filename,
            "name": self.filename.rsplit(".", 1)[0] if self.filename else "",
            "ext": self.filename.rsplit(".", 1)[-1] if "." in self.filename else "",
            "size": human_size(self.size),
            "size_bytes": str(int(self.size or 0)),
            "title": self.title or self.filename,
            "source": self.source,
            "chat": self.chat_title,
            "message_id": str(self.message_id),
            "date": stamp.strftime("%Y-%m-%d"),
            "time": stamp.strftime("%H:%M"),
            "year": stamp.strftime("%Y"),
            "duration": duration(self.duration_seconds),
            **self.extra,
        }


def render_caption(
    template: str,
    context: CaptionContext,
    *,
    escape: bool = True,
    limit: int = LIMIT,
) -> str:
    """Render placeholders; unknown ones are left visible for the admin to see."""
    values = context.values()

    def substitute(match: re.Match[str]) -> str:
        raw_value = values.get(match.group(1))
        if raw_value is None:
            return match.group(0)
        return html.escape(str(raw_value)) if escape else str(raw_value)

    text = PLACEHOLDER_RE.sub(substitute, template or "")
    return text[:limit]


@dataclass
class WordRules:
    delete: list[str] = field(default_factory=list)
    replace: list[str] = field(default_factory=list)  # "old=>new" entries

    @property
    def empty(self) -> bool:
        return not self.delete and not self.replace

    def apply(self, text: str) -> str:
        result = text or ""
        for rule in self.replace:
            if "=>" not in rule:
                continue
            old, _, new = rule.partition("=>")
            old = old.strip()
            if not old:
                continue
            result = re.sub(re.escape(old), new, result, flags=re.IGNORECASE)
        for word in self.delete:
            word = word.strip()
            if not word:
                continue
            result = re.sub(re.escape(word), "", result, flags=re.IGNORECASE)
        result = re.sub(r"[ \t]{2,}", " ", result)
        result = re.sub(r"\n{3,}", "\n\n", result)
        return "\n".join(line.rstrip() for line in result.splitlines()).strip()

    def describe(self) -> str:
        parts = []
        if self.delete:
            parts.append("delete: " + ", ".join(self.delete))
        if self.replace:
            parts.append("replace: " + ", ".join(self.replace))
        return " | ".join(parts) or "no rules"


def parse_word_input(argument: str) -> tuple[str, list[str]]:
    """``/delword foo bar`` → ``("delete", ["foo", "bar"])``."""
    action, _, rest = (argument or "").partition(" ")
    words = [word for word in re.split(r"[,\s]+", rest) if word.strip()]
    return action.strip().lower(), words


__all__ = [
    "DEFAULT_CAPTION",
    "LIMIT",
    "CaptionContext",
    "WordRules",
    "duration",
    "human_size",
    "parse_word_input",
    "render_caption",
]
