"""Filters & Triggers plugin: per-group keyword → response automation."""

from __future__ import annotations

from .manifest import MANIFEST

import re

from aiogram import F, Router
from aiogram.types import InlineKeyboardMarkup, Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.ui import Btn, keyboard, message



@plugin
class FiltersPlugin(MeikoPlugin):
    manifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        router = Router(name="plugin:filters")
        router.message.register(self.on_message, F.chat.type.in_({"group", "supergroup"}))
        ctx.add_router(router)

    async def add_trigger(self, group_key: str, trigger: str, response: str, match_type: str = "exact") -> dict:
        ctx = self.context
        assert ctx is not None
        triggers = dict(await ctx.get_state(f"triggers:{group_key}", {}) or {})
        if match_type == "regex" and not bool(self.config("allow_regex", False)):
            raise ValueError("regex triggers are disabled by the owner")
        if len(triggers) >= int(self.config("max_triggers", 100)):
            raise ValueError("trigger limit reached")
        triggers[trigger.lower()] = {"response": response, "type": match_type}
        await ctx.set_state(f"triggers:{group_key}", triggers)
        return triggers[trigger.lower()]

    async def match(self, group_key: str, text: str) -> dict | None:
        ctx = self.context
        assert ctx is not None
        triggers: dict = await ctx.get_state(f"triggers:{group_key}", {}) or {}
        lowered = text.strip().lower()
        for trigger, spec in triggers.items():
            kind = spec.get("type", "exact")
            if kind == "exact" and lowered == trigger:
                return spec
            if kind == "contains" and trigger in lowered:
                return spec
            if kind == "regex":
                try:
                    if re.search(trigger, text, re.IGNORECASE):
                        return spec
                except re.error:
                    continue
        return None

    async def on_message(self, message_obj: Message) -> None:
        ctx = self.context
        if ctx is None or not message_obj.text or message_obj.from_user is None:
            return
        if message_obj.from_user.is_bot:
            return
        group_key = str(message_obj.chat.id)
        spec = await self.match(group_key, message_obj.text)
        if spec is None:
            return
        buttons = spec.get("buttons") or []
        markup = None
        if buttons:
            markup = keyboard(
                [[Btn(b["text"], url=b.get("url"), callback_data=b.get("callback_data"))] for b in buttons],
                premium_icons=False,
            )
        builder = message().line(spec["response"])
        await ctx.bot.send_message(
            message_obj.chat.id,
            builder.build(),
            reply_markup=markup,
        )


__all__ = ["FiltersPlugin", "MANIFEST", "InlineKeyboardMarkup"]
