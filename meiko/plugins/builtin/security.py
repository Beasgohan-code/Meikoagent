"""Security plugin: captcha-style verification, lock-down and raid guard."""

from __future__ import annotations

import secrets

from aiogram import F, Router
from aiogram.types import CallbackQuery, ChatMemberUpdated
from aiogram.types import ChatPermissions

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.ui import Btn, keyboard, message

MANIFEST = PluginManifest(
    slug="security",
    name="Security & Verification",
    version="1.0.0",
    description="New-member verification buttons, lockdown mode and join-raid detection.",
    category="security",
    icon="lock",
    permissions=["messages.send", "moderation.write", "messages.delete", "groups.read", "storage.write"],
    commands=["verify", "lockdown"],
    events=["join", "callback"],
    config_schema={
        "verification_enabled": {"type": "bool", "default": True, "label": "Require verification"},
        "timeout_minutes": {"type": "int", "default": 10, "label": "Verification timeout"},
        "raid_join_threshold": {"type": "int", "default": 15, "label": "Joins per minute to trigger lockdown"},
    },
)

LOCKED = ChatPermissions(can_send_messages=False)
OPEN = ChatPermissions(can_send_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)


@plugin
class SecurityPlugin(MeikoPlugin):
    manifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        router = Router(name="plugin:security")
        router.chat_member.register(self.on_join)
        router.callback_query.register(self.on_callback, F.data.startswith("verify:"))
        ctx.add_router(router)

    async def issue_challenge(self, chat_id: int, user_id: int) -> str:
        ctx = self.context
        assert ctx is not None
        token = secrets.token_urlsafe(8)[:16]
        await ctx.set_state(f"challenge:{chat_id}:{user_id}", token, ttl=int(self.config("timeout_minutes", 10)) * 60)
        return token

    async def on_join(self, update: ChatMemberUpdated) -> None:
        ctx = self.context
        if ctx is None or not bool(self.config("verification_enabled", True)):
            return
        if update.old_chat_member.status not in ("left", "kicked"):
            return
        token = await self.issue_challenge(update.chat.id, update.new_chat_member.user.id)
        builder = (
            message("Verification required", "shield")
            .subtitle("Tap the button to prove you are human.")
            .key_value("Member", update.new_chat_member.user.id, "user")
        )
        await ctx.bot.send_message(
            update.chat.id,
            builder.build(),
            reply_markup=keyboard(
                [[Btn("I am human", callback_data=f"verify:{token}", emoji="check", style="success")]]
            ),
        )

    async def on_callback(self, call: CallbackQuery) -> None:
        ctx = self.context
        if ctx is None or call.message is None:
            return
        token = (call.data or "").partition(":")[2]
        expected = await ctx.get_state(f"challenge:{call.message.chat.id}:{call.from_user.id}")
        if not expected or token != expected:
            await call.answer("Challenge expired – ask an admin to verify you.", show_alert=True)
            return
        await ctx.delete_state(f"challenge:{call.message.chat.id}:{call.from_user.id}")
        await call.answer("Verified ✅")
        await call.message.edit_text(message("Verified", "check").build())

    async def lockdown(self, chat_id: int, enabled: bool) -> None:
        ctx = self.context
        assert ctx is not None
        await ctx.bot.set_chat_permissions(chat_id, LOCKED if enabled else OPEN)
        ctx.log("warning", "lockdown toggled", chat=chat_id, enabled=enabled)


__all__ = ["SecurityPlugin", "MANIFEST"]
