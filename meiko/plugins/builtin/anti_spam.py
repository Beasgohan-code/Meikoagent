"""Anti-Spam plugin – flood/repeat/link/invite/keyword protection."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from meiko.moderation.antispam import AntiSpamEngine, AntiSpamPolicy
from meiko.moderation.events import ModerationEvent
from meiko.moderation.rules import Rule, RuleEngine
from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.security.permissions import Role
from meiko.ui import keyboard, message, Btn

MANIFEST = PluginManifest(
    slug="anti_spam",
    name="Anti-Spam Shield",
    version="1.0.0",
    description="Flood, duplicate, link, invite, mention and keyword protection with escalation.",
    category="security",
    icon="shield",
    permissions=["moderation.read", "moderation.write", "messages.send", "messages.delete"],
    commands=["antispam", "spamcheck"],
    events=["message"],
    config_schema={
        "flood_messages": {"type": "int", "default": 7, "label": "Messages per window"},
        "flood_window_seconds": {"type": "int", "default": 8, "label": "Window (seconds)"},
        "action": {
            "type": "choice",
            "choices": ["warn", "mute", "kick", "ban"],
            "default": "mute",
            "label": "Default punishment",
        },
        "block_links": {"type": "bool", "default": True, "label": "Delete links"},
        "block_invites": {"type": "bool", "default": True, "label": "Delete invite links"},
        "blocked_keywords": {"type": "list", "default": [], "label": "Blocked keywords"},
    },
    resources={"cpu": "low"},
)


@plugin
class AntiSpamPlugin(MeikoPlugin):
    manifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self.engine: AntiSpamEngine | None = None
        self.rules: RuleEngine | None = None

    def policy(self) -> AntiSpamPolicy:
        return AntiSpamPolicy(
            flood_messages=int(self.config("flood_messages", 7)),
            flood_window_seconds=int(self.config("flood_window_seconds", 8)),
            links_enabled=bool(self.config("block_links", True)),
            invites_enabled=bool(self.config("block_invites", True)),
            keywords_enabled=bool(self.config("blocked_keywords")),
            blocked_keywords=list(self.config("blocked_keywords", []) or []),
            action=str(self.config("action", "mute")),
        )

    async def on_start(self, ctx: PluginContext) -> None:
        self.engine = AntiSpamEngine(ctx.cache)
        self.rules = RuleEngine(self._execute, self.engine)
        router = Router(name="plugin:anti_spam")
        router.message.register(self.on_message, F.chat.type.in_({"group", "supergroup"}))
        ctx.add_router(router)

    async def on_stop(self, ctx: PluginContext) -> None:
        self.engine = None
        self.rules = None

    async def _execute(self, action, event, rule):  # pragma: no cover - needs bot
        ctx = self.context
        if ctx is None:
            return {"ok": False, "reason": "stopped"}
        ctx.log("info", "anti-spam action", action=action.get("type"), user=event.user_id)
        return {"ok": True, "action": action.get("type")}

    async def on_message(self, message_obj: Message) -> None:
        ctx = self.context
        if ctx is None or self.rules is None or message_obj.from_user is None:
            return
        if message_obj.from_user.is_bot:
            return
        text = message_obj.text or message_obj.caption or ""
        event = ModerationEvent(
            kind="message",
            bot_id=ctx.bot_id or 0,
            chat_id=message_obj.chat.id,
            user_id=message_obj.from_user.id,
            message_id=message_obj.message_id,
            text=text,
            message_thread_id=message_obj.message_thread_id,
            raw={"source": "plugin:anti_spam"},
        )
        verdicts = await self.engine.evaluate(event, self.policy()) if self.engine else []
        if verdicts:
            worst = verdicts[0]
            ctx.log("warning", "spam detected", user=event.user_id, rule=worst.rule)
            await ctx.set_state(f"last:{event.user_id}", {"rule": worst.rule, "reason": worst.reason}, ttl=600)

    async def on_config_change(self, ctx: PluginContext, previous) -> None:
        ctx.log("info", "anti-spam policy updated", previous=previous)


HELP_TEXT = message("Anti-Spam Shield", "shield").subtitle(
    "Flood → repeat → link → invite → keyword detection with escalation."
).build()

HELP_KEYBOARD = keyboard(
    [
        [Btn("Test a message", callback_data="spam:test", emoji="search", style="primary")],
        [Btn("Policy", callback_data="spam:policy", emoji="gear")],
    ]
)

__all__ = ["AntiSpamPlugin", "MANIFEST", "Role"]
