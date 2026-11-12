"""Welcome / Goodbye plugin: greeting cards, verification and custom buttons."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import ChatMemberUpdated, Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.ui import Btn, keyboard, message, user_mention

from .manifest import MANIFEST


@plugin
class WelcomePlugin(MeikoPlugin):
    manifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        router = Router(name="plugin:welcome")
        router.chat_member.register(self.on_chat_member)
        router.message.register(self.on_message, F.chat.type.in_({"group", "supergroup"}))
        ctx.add_router(router)
        ctx.log("info", "welcome plugin ready")

    async def on_stop(self, ctx: PluginContext) -> None:
        ctx.log("info", "welcome plugin stopped")

    def render(self, template: str, user, chat) -> str:
        text = (
            template.replace("{user}", user_mention(user.id, user.first_name or "there"))
            .replace("{chat}", chat.title or "the chat")
            .replace("{mention}", user_mention(user.id, user.first_name or "there"))
        )
        builder = message()
        builder.line(text)
        builder.line("", raw=True)
        builder.line(f"<i>Powered by MEIKO AGENT</i>", raw=True)
        return builder.build()

    def markup(self):
        label = str(self.config("button_text", "") or "")
        url = str(self.config("button_url", "") or "")
        if label and url:
            return keyboard([[Btn(label, url=url, emoji="arrow")]], premium_icons=False)
        return None

    async def on_chat_member(self, update: ChatMemberUpdated) -> None:
        ctx = self.context
        if ctx is None or ctx._bot is None:
            return
        old_status, new_status = update.old_chat_member.status, update.new_chat_member.status
        if old_status in ("left", "kicked") and new_status == "member":
            text = self.render(
                str(self.config("welcome_text", "Welcome {user}!")), update.new_chat_member.user, update.chat
            )
            await ctx.bot.send_message(update.chat.id, text, reply_markup=self.markup())
        elif new_status in ("left", "kicked") and bool(self.config("goodbye_enabled", False)):
            text = self.render(
                str(self.config("goodbye_text", "{user} left the chat.")),
                update.old_chat_member.user,
                update.chat,
            )
            await ctx.bot.send_message(update.chat.id, text)

    async def on_message(self, message_obj: Message) -> None:
        ctx = self.context
        if ctx is None:
            return
        await ctx.set_state("last_seen", message_obj.message_id, ttl=3600)


__all__ = ["WelcomePlugin", "MANIFEST"]
