"""Telegram link parsing for the post saver.

Supports everything Videl1's saver accepts and a bit more:

* public posts      ``https://t.me/channel/123``
* private posts     ``https://t.me/c/123456789/42``  (→ chat id ``-100123456789``)
* forum topics      ``https://t.me/c/123456789/7/42`` or ``t.me/channel/7/42``
* ranges            ``…/100-120`` (inclusive)
* lists             ``…/100,102,105``
* open ranges       ``…/100-`` (to the last message) and ``…/-120`` (from the first)
* bare ids          ``100-105`` when a chat is supplied by the caller
* usernames         ``@channel`` / ``channel`` when ``allow_bare_username`` is set

The module is pure: no aiogram, no database, no network.  ``plan_links`` turns a
blob of text into fully expanded, de-duplicated message ids in original order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LINK_RE = re.compile(
    r"""(?:https?://)?(?:www\.)?t\.me/(?:
        c/(?P<internal>\d{1,})            # private supergroup
      | (?P<username>[A-Za-z][A-Za-z0-9_]{3,31})
    )
    (?P<rest>(?:/[-\d,]+)*)""",
    re.VERBOSE | re.IGNORECASE,
)
BARE_SPEC_RE = re.compile(r"^\s*(?P<start>\d{0,8})(?:\s*-\s*(?P<end>\d{0,8}))?\s*$")
PRIVATE_PREFIX = "-100"
MAX_RANGE = 500


@dataclass(frozen=True)
class MessageRange:
    """An inclusive span of message ids."""

    start: int
    end: int

    def __post_init__(self) -> None:  # pragma: no cover - dataclass freeze
        object.__setattr__(self, "start", min(self.start, self.end))
        object.__setattr__(self, "end", max(self.start, self.end))

    @property
    def count(self) -> int:
        return self.end - self.start + 1

    def ids(self, limit: int = MAX_RANGE) -> list[int]:
        return list(range(self.start, min(self.end, self.start + limit - 1) + 1))

    def __str__(self) -> str:
        return f"{self.start}-{self.end}"


@dataclass
class LinkTarget:
    chat: int | str
    ranges: list[MessageRange] = field(default_factory=list)
    topic_id: int | None = None
    raw: str = ""
    private: bool = False

    @property
    def message_ids(self) -> list[int]:
        seen: set[int] = set()
        ordered: list[int] = []
        for span in self.ranges:
            for message_id in span.ids():
                if message_id not in seen:
                    seen.add(message_id)
                    ordered.append(message_id)
        return ordered

    @property
    def count(self) -> int:
        return len(self.message_ids)

    @property
    def label(self) -> str:
        if isinstance(self.chat, int):
            name = str(self.chat).removeprefix(PRIVATE_PREFIX)
            base = f"t.me/c/{name}"
        else:
            base = f"t.me/{self.chat}"
        if self.topic_id:
            base = f"{base}/{self.topic_id}"
        if len(self.ranges) == 1:
            base = f"{base}/{self.ranges[0]}"
        elif self.ranges:
            base = f"{base}/{self.ranges[0]} (+{len(self.ranges) - 1} spans)"
        return base


@dataclass
class PlanIssue:
    raw: str
    reason: str


@dataclass
class LinkPlan:
    targets: list[LinkTarget] = field(default_factory=list)
    issues: list[PlanIssue] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(target.count for target in self.targets)

    @property
    def ok(self) -> bool:
        return bool(self.targets)

    def describe(self, limit: int = 5) -> str:
        lines = [f"{target.label} → {target.count} message(s)" for target in self.targets[:limit]]
        if len(self.targets) > limit:
            lines.append(f"… and {len(self.targets) - limit} more link(s)")
        lines.extend(f"! {issue.raw}: {issue.reason}" for issue in self.issues[:limit])
        return "\n".join(lines) or "nothing to save"


def parse_message_spec(spec: str, raw: str = "") -> list[MessageRange]:
    """Parse ``12``, ``12-20``, ``12,14,16``, ``12-`` or ``-20``."""
    spans: list[MessageRange] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = BARE_SPEC_RE.match(chunk)
        if not match:
            continue
        start_text = match.group("start")
        end_text = match.group("end")
        if "-" in chunk:
            start = int(start_text) if start_text else 1
            end = int(end_text) if end_text else start + MAX_RANGE - 1
        else:
            if not start_text:
                continue
            start = end = int(start_text)
        if start <= 0:
            continue
        spans.append(MessageRange(start, end))
    return spans


def parse_link(raw: str, *, allow_bare_username: bool = False) -> LinkTarget | None:
    text = raw.strip().strip("<>()[]\"',")
    if not text:
        return None
    match = LINK_RE.search(text)
    if match:
        internal = match.group("internal")
        username = match.group("username")
        rest = (match.group("rest") or "").strip("/")
        if internal:
            chat: int | str = int(f"{PRIVATE_PREFIX}{internal}")
            private = True
            parts = [part for part in rest.split("/") if part]
            topic_id = None
            if len(parts) >= 2:
                topic_id = int(parts[0])
                parts = parts[1:]
            spec = parts[0] if parts else ""
        else:
            chat = username
            private = False
            parts = [part for part in rest.split("/") if part]
            topic_id = None
            if len(parts) >= 2:
                topic_id = int(parts[0])
                parts = parts[1:]
            spec = parts[0] if parts else ""
        ranges = parse_message_spec(spec, raw) if spec else []
        return LinkTarget(chat=chat, ranges=ranges, topic_id=topic_id, raw=raw, private=private)

    if allow_bare_username:
        handle = re.match(r"^@?(?P<name>[A-Za-z][A-Za-z0-9_]{3,31})$", text)
        if handle:
            return LinkTarget(chat=handle.group("name"), raw=raw)
    return None


def plan_links(text: str, *, allow_bare_username: bool = False) -> LinkPlan:
    """Turn free text (spaces, newlines, commas) into a de-duplicated plan."""
    plan = LinkPlan()
    chunks = [chunk for chunk in re.split(r"[\s\n\r;]+", text or "") if chunk.strip()]
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        index += 1
        target = parse_link(chunk, allow_bare_username=allow_bare_username)
        if target is None:
            plan.issues.append(PlanIssue(chunk, "not a Telegram post link"))
            continue
        # bare ranges after a link inherit its chat: "t.me/x/10 11-15"
        while index < len(chunks) and re.fullmatch(r"-?\d{1,8}(?:-\d{0,8})?", chunks[index]):
            target.ranges.extend(parse_message_spec(chunks[index]))
            index += 1
        if not target.ranges:
            plan.issues.append(PlanIssue(chunk, "link has no message id"))
            continue
        plan.targets.append(target)
    return plan


def chunks_of(ids: list[int], size: int) -> list[list[int]]:
    """Split ids into batches (Telegram-friendly progress updates)."""
    if size <= 0:
        return [ids]
    return [ids[position : position + size] for position in range(0, len(ids), size)]


__all__ = [
    "LINK_RE",
    "MAX_RANGE",
    "LinkPlan",
    "LinkTarget",
    "MessageRange",
    "PlanIssue",
    "chunks_of",
    "parse_link",
    "parse_message_spec",
    "plan_links",
]
