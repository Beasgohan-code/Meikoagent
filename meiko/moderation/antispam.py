"""Per-group anti-spam detectors.

Each detector is a pure function over a :class:`ModerationEvent` plus lightweight
per-chat state held in Redis (with the in-memory fallback).  Detectors return a
:class:`SpamVerdict` (or ``None``) so the rule engine can decide the action –
detection never bans anybody on its own.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

from meiko.cache import Cache, get_cache
from meiko.moderation.events import ModerationEvent

URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
TELEGRAM_INVITE_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(?:\+|joinchat/)[\w-]+", re.IGNORECASE
)
MENTION_RE = re.compile(r"(?<!\w)@\w{3,}")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


@dataclass(frozen=True)
class SpamVerdict:
    rule: str
    severity: str  # low / medium / high
    reason: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class AntiSpamPolicy:
    """Stored on ``GroupSettings.antispam`` as a plain dict."""

    flood_enabled: bool = True
    flood_messages: int = 7
    flood_window_seconds: int = 8
    duplicate_enabled: bool = True
    duplicate_threshold: int = 3
    links_enabled: bool = True
    allow_links: bool = False
    invites_enabled: bool = True
    mentions_enabled: bool = True
    max_mentions: int = 5
    keywords_enabled: bool = False
    blocked_keywords: list[str] = field(default_factory=list)
    suspicious_join_enabled: bool = True
    new_account_seconds: int = 3600
    caps_enabled: bool = False
    caps_percent: int = 70
    emoji_enabled: bool = False
    max_emoji: int = 20
    ignored_roles: list[str] = field(default_factory=lambda: ["admin", "moderator", "trusted"])
    action: str = "warn"  # warn | mute | kick | ban | delete
    mute_seconds: int = 300
    warn_threshold: int = 3

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AntiSpamPolicy":
        policy = cls()
        for key, value in (data or {}).items():
            if hasattr(policy, key) and value is not None:
                setattr(policy, key, value)
        return policy

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _keyword_hit(text: str, keywords: list[str]) -> str | None:
    lowered = (text or "").lower()
    for keyword in keywords:
        if keyword and keyword.lower() in lowered:
            return keyword
    return None


class AntiSpamEngine:
    """Stateful detector runner backed by the cache adapter."""

    def __init__(self, cache: Cache | None = None) -> None:
        self.cache = cache or get_cache()

    async def evaluate(
        self, event: ModerationEvent, policy: AntiSpamPolicy
    ) -> list[SpamVerdict]:
        verdicts: list[SpamVerdict] = []
        text = event.text or ""

        if policy.duplicate_enabled and event.user_id:
            digest = hashlib.sha256(text.encode()).hexdigest()[:16]
            key = f"dup:{event.bot_id}:{event.chat_id}:{event.user_id}:{digest}"
            count = await self.cache.incr(key, ttl=300)
            if count >= policy.duplicate_threshold and text:
                verdicts.append(
                    SpamVerdict(
                        "duplicate",
                        "medium",
                        f"the same message was sent {count} times",
                        {"count": count},
                    )
                )

        if policy.flood_enabled and event.user_id:
            allowed, retry_after = await self.cache.rate_limit(
                f"flood:{event.bot_id}:{event.chat_id}:{event.user_id}",
                limit=policy.flood_messages,
                window=policy.flood_window_seconds,
            )
            if not allowed:
                verdicts.append(
                    SpamVerdict(
                        "flood",
                        "high",
                        f"more than {policy.flood_messages} messages in "
                        f"{policy.flood_window_seconds}s",
                        {"retry_after": retry_after},
                    )
                )

        if policy.invites_enabled and TELEGRAM_INVITE_RE.search(text):
            verdicts.append(SpamVerdict("invite_link", "high", "telegram invite link"))

        if policy.links_enabled and not policy.allow_links and URL_RE.search(text):
            verdicts.append(SpamVerdict("link", "medium", "external link"))

        if policy.mentions_enabled and event.mentions_count > policy.max_mentions:
            verdicts.append(
                SpamVerdict(
                    "mention_spam",
                    "medium",
                    f"{event.mentions_count} mentions in one message",
                    {"mentions": event.mentions_count},
                )
            )

        if policy.keywords_enabled and policy.blocked_keywords:
            hit = _keyword_hit(text, policy.blocked_keywords)
            if hit:
                verdicts.append(
                    SpamVerdict("blocked_keyword", "medium", "blocked keyword", {"keyword": hit})
                )

        if policy.caps_enabled and len(text) >= 20:
            letters = [c for c in text if c.isalpha()]
            if letters:
                caps = sum(1 for c in letters if c.isupper()) / len(letters) * 100
                if caps >= policy.caps_percent:
                    verdicts.append(
                        SpamVerdict("caps", "low", f"{int(caps)}% capital letters")
                    )

        if policy.emoji_enabled:
            emojis = len(EMOJI_RE.findall(text))
            if emojis > policy.max_emoji:
                verdicts.append(
                    SpamVerdict("emoji_spam", "low", f"{emojis} emojis", {"count": emojis})
                )

        return verdicts

    async def suspicious_join(
        self, event: ModerationEvent, policy: AntiSpamPolicy
    ) -> SpamVerdict | None:
        if not policy.suspicious_join_enabled:
            return None
        recent = event.joined_within_seconds
        if recent is not None and recent < policy.new_account_seconds:
            return SpamVerdict(
                "suspicious_join",
                "medium",
                "very new account joined",
                {"age_seconds": recent},
            )
        if not event.username and not event.is_bot:
            # account without username joining fast-moving groups is a common raid signal
            key = f"join:{event.bot_id}:{event.chat_id}"
            count = await self.cache.incr(key, ttl=60)
            if count >= 10:
                return SpamVerdict(
                    "join_raid", "high", f"{count} joins in one minute", {"count": count}
                )
        return None


def is_ignored(event: ModerationEvent, policy: AntiSpamPolicy) -> bool:
    """Admins/moderators/trusted members are never auto-punished."""
    if event.is_bot and "bot" in policy.ignored_roles:
        return True
    if event.is_admin and "admin" in policy.ignored_roles:
        return True
    if event.is_trusted and "trusted" in policy.ignored_roles:
        return True
    return False


class ReputationTracker:
    """Rolling spam score per member, used for warning escalation."""

    def __init__(self, cache: Cache | None = None, ttl: int = 86_400) -> None:
        self.cache = cache or get_cache()
        self.ttl = ttl

    async def add(self, bot_id: int, chat_id: int, user_id: int, points: int = 1) -> int:
        key = f"rep:{bot_id}:{chat_id}:{user_id}"
        value = await self.cache.incr(key, ttl=self.ttl)
        return value

    async def get(self, bot_id: int, chat_id: int, user_id: int) -> int:
        return int(await self.cache.get(f"rep:{bot_id}:{chat_id}:{user_id}", 0) or 0)

    async def reset(self, bot_id: int, chat_id: int, user_id: int) -> None:
        await self.cache.delete(f"rep:{bot_id}:{chat_id}:{user_id}")


def now() -> float:  # pragma: no cover - helper for callers
    return time.time()
