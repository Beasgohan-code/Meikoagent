"""Premium custom-emoji catalogue for MEIKO AGENT.

The document ids below are the verified ids published in
``Beasgohan-code/Videl0`` (``videl/utils/premium_emojis.py`` and
``webapp/src/lib/premium-emoji.ts``).  Telegram only renders ``<tg-emoji>``
entities when the *bot* is allowed to use custom emoji (the bot owner holds
Telegram Premium and enabled it in BotFather).  Every helper therefore has a
plain unicode fallback so messages stay readable on every client.

Usage::

    from meiko.ui.emoji import E, emoji, html

    emoji("shield")        -> "🛡️"               (plain, always safe)
    html.emoji("shield")   -> '<tg-emoji emoji-id="6030445631921721471">🛡️</tg-emoji>'
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

#: ``name -> (document_id, unicode fallback)``
PREMIUM_EMOJI: dict[str, tuple[str, str]] = {
    # core cyber icon set (Videl0 PREMIUM_IDS + FALLBACK)
    "orb": ("5042302287087666158", "🔮"),
    "diamond": ("5427168083074628963", "💎"),
    "line": ("5456140674028019486", "➖"),
    "spark": ("5325547803936572038", "✨"),
    "hex": ("5251203410396458957", "⬡"),
    "arrow": ("5416117059207572332", "➡️"),
    "check": ("5084979757905347540", "✅"),
    "cross": ("5210952531676504517", "❌"),
    "warn": ("5447644880824181073", "⚠️"),
    "ban": ("5260293700088511294", "🚫"),
    "lock": ("5296369303661067030", "🔒"),
    "shield": ("6030445631921721471", "🛡️"),
    "crown": ("5217822164362739968", "👑"),
    "stats": ("5231200819986047254", "📊"),
    "robot": ("6030400221232501136", "🤖"),
    # numbered / secondary packs (Videl0 ADDITIONAL_EMOJIS)
    "fire": ("5413613476334192594", "🔥"),
    "star": ("5427201476847465985", "⭐"),
    "heart": ("5429126552200700007", "❤️"),
    "clock": ("5343863823230952832", "⏰"),
    "calendar": ("5413634892084188038", "📅"),
    "mail": ("5368808590600748258", "✉️"),
    "link": ("5397842136791404896", "🔗"),
    "trash": ("5445351100049512183", "🗑️"),
    "hammer": ("5431880116131410766", "🔨"),
    "user": ("5373036007794358183", "👤"),
    "users": ("5373328827212212482", "👥"),
    "message": ("5443038326535759644", "💬"),
    "bell": ("5458666250834138261", "🔔"),
    "search": ("5393043212897174356", "🔍"),
    "gear": ("5341715473882955310", "⚙️"),
    "plug": ("5447410659077661506", "🔌"),
    "rocket": ("5433672972221942658", "🚀"),
    "book": ("5348125953090403204", "📖"),
    "code": ("5375136778328049525", "💻"),
    "key": ("5444980743319929542", "🔑"),
    "eye": ("5373401953171885116", "👁️"),
    "gift": ("5413683375113686200", "🎁"),
    "coin": ("5375215593947470362", "🪙"),
    "graph": ("5372926953978341366", "📈"),
    "download": ("5409286744882215399", "⬇️"),
    "upload": ("5409286946300899849", "⬆️"),
    "wifi": ("5375189794022794444", "📶"),
    "server": ("5375235598304028890", "🖥️"),
    "database": ("5447410659077661506", "🗄️"),
    "pin": ("5393170786364465266", "📌"),
    "flag": ("5449629076144053456", "🚩"),
    "gavel": ("5425026489688769338", "⚖️"),
    "mute": ("5260293700088511294", "🔇"),
    "unmute": ("5084979757905347540", "🔊"),
    "id": ("5447410659077661506", "🆔"),
    "sparkle": ("5325547803936572038", "✨"),
    "verified": ("5458451487842907466", "☑️"),
    "admin": ("6030626076528252987", "🛡"),
    "premium": ("5427168083074628963", "💎"),
    "loading": ("5319136744473552991", "⏳"),
    "sleep": ("5319164443015890251", "💤"),
    # media / automation pack (Videl feature set)
    "music": ("5375243960633718526", "🎵"),
    "video": ("5375232960956774875", "🎬"),
    "photo": ("5372926953978341366", "🖼️"),
    "folder": ("5447410659077661506", "📁"),
    "file": ("5408848727763389775", "📄"),
    "type": ("5375136778328049525", "🔤"),
    "palette": ("5319136744473552991", "🎨"),
    "refresh": ("5368808590600748258", "🔄"),
    "plus": ("5416117059207572332", "➕"),
}

#: Semantic aliases so copy decks can stay stable while ids evolve.
ALIASES: dict[str, str] = {
    "success": "check",
    "error": "cross",
    "warning": "warn",
    "blocked": "ban",
    "private": "lock",
    "security": "shield",
    "owner": "crown",
    "analytics": "stats",
    "bot": "robot",
    "time": "clock",
    "schedule": "calendar",
    "webhook": "link",
    "delete": "trash",
    "moderation": "gavel",
    "plugin": "plug",
    "deploy": "rocket",
    "docs": "book",
    "developer": "code",
    "apikey": "key",
    "monitor": "eye",
    "runtime": "server",
    "db": "database",
    "queue": "graph",
    "muted": "mute",
    "unmuted": "unmute",
    "active": "check",
    "inactive": "cross",
    "audio": "music",
    "song": "music",
    "tag": "type",
    "rename": "type",
    "saver": "download",
    "heartbeat": "bell",
    "watchdog": "monitor",
    "keepalive": "heart",
    "caption": "message",
    "post": "message",
    "storage": "folder",
}


@dataclass(frozen=True)
class Emoji:
    name: str
    document_id: str
    fallback: str

    @property
    def html(self) -> str:
        """HTML entity understood by ``parse_mode="HTML"``."""
        return f'<tg-emoji emoji-id="{self.document_id}">{self.fallback}</tg-emoji>'

    def __str__(self) -> str:  # plain text form
        return self.fallback


@lru_cache(maxsize=512)
def get(name: str) -> Emoji:
    key = ALIASES.get(name, name)
    if key in PREMIUM_EMOJI:
        doc_id, fallback = PREMIUM_EMOJI[key]
        return Emoji(key, doc_id, fallback)
    # Unknown name: neutral diamond keeps layouts stable instead of crashing.
    doc_id, fallback = PREMIUM_EMOJI["diamond"]
    return Emoji(key, doc_id, fallback)


def emoji(name: str) -> str:
    """Plain unicode fallback."""
    return get(name).fallback


def premium_html(name: str) -> str:
    """``<tg-emoji>`` markup for HTML parse mode."""
    return get(name).html


def document_id(name: str) -> str:
    return get(name).document_id


def available() -> Iterable[str]:
    return PREMIUM_EMOJI.keys()


class _Html:
    """Tiny namespace so templates read naturally: ``html.shield``."""

    def __getattr__(self, item: str) -> str:
        return premium_html(item)

    def __call__(self, item: str) -> str:
        return premium_html(item)


html = _Html()


class _Plain:
    def __getattr__(self, item: str) -> str:
        return emoji(item)

    def __call__(self, item: str) -> str:
        return emoji(item)


E = _Plain()


def strip_premium(text: str) -> str:
    """Convert ``<tg-emoji>`` markup into its plain unicode fallback."""
    import re

    return re.sub(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', r"\1", text)
