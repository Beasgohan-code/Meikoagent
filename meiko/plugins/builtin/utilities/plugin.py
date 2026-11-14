"""Utilities plugin: small but genuinely useful group tools."""

from __future__ import annotations

from .manifest import MANIFEST

import time

from aiogram import F, Router
from aiogram.types import Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.ui import Btn, keyboard, message



@plugin
class UtilitiesPlugin(MeikoPlugin):
    manifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        router = Router(name="plugin:utilities")
        router.message.register(self.on_message, F.chat.type.in_({"group", "supergroup", "private"}))
        ctx.add_router(router)

    async def on_message(self, message_obj: Message) -> None:
        ctx = self.context
        if ctx is None or not message_obj.text:
            return
        text = message_obj.text.strip()
        if text.startswith("/ping"):
            started = time.perf_counter()
            sent = await ctx.bot.send_message(message_obj.chat.id, message("Pong", "spark").build())
            latency = (time.perf_counter() - started) * 1000
            await self.edit_text(
                sent, message("Pong", "spark").key_value("Latency", f"{latency:.0f} ms").build()
            )
        elif text.startswith("/id") and message_obj.from_user:
            builder = (
                message("Identifiers", "id")
                .key_value("Your ID", message_obj.from_user.id)
                .key_value("Chat ID", message_obj.chat.id)
            )
            await ctx.bot.send_message(message_obj.chat.id, builder.build())
        elif text.startswith("/note ") and bool(self.config("allow_notes", True)):
            _, _, body = text.partition(" ")
            name, _, content = body.partition("=")
            if not name.strip() or not content.strip():
                await ctx.bot.send_message(message_obj.chat.id, "Usage: /note name = text")
                return
            notes = dict(await ctx.get_state(f"notes:{message_obj.chat.id}", {}) or {})
            if len(notes) >= int(self.config("max_notes", 50)):
                await ctx.bot.send_message(message_obj.chat.id, "⚠️ note limit reached")
                return
            notes[name.strip().lower()] = content.strip()
            await ctx.set_state(f"notes:{message_obj.chat.id}", notes)
            await ctx.bot.send_message(message_obj.chat.id, f"✅ Note <b>{name.strip()}</b> saved.")
        elif text.startswith("/notes"):
            notes = dict(await ctx.get_state(f"notes:{message_obj.chat.id}", {}) or {})
            builder = message("Saved notes", "book")
            if notes:
                for name in sorted(notes):
                    builder.bullet(name, "pin")
            else:
                builder.line("No notes saved yet.")
            await ctx.bot.send_message(message_obj.chat.id, builder.build())


__all__ = ["UtilitiesPlugin", "MANIFEST"]
