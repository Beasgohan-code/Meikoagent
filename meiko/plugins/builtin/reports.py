"""Reports & Appeals plugin: member reports, moderator queue and appeals."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.ui import Btn, keyboard, message, user_mention

MANIFEST = PluginManifest(
    slug="reports",
    name="Reports & Appeals",
    version="1.0.0",
    description="Members report messages; moderators resolve from an inline queue and appeals.",
    category="moderation",
    icon="flag",
    permissions=["messages.send", "moderation.read", "storage.read", "storage.write"],
    commands=["report", "reports", "appeal"],
    events=["message", "callback"],
    config_schema={
        "auto_forward_to_admins": {"type": "bool", "default": True, "label": "Notify admins"},
        "rate_limit_minutes": {"type": "int", "default": 5, "label": "Report cooldown (minutes)"},
    },
)


@plugin
class ReportsPlugin(MeikoPlugin):
    manifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        router = Router(name="plugin:reports")
        router.message.register(self.on_report_command, F.text.startswith("/report"), F.chat.type.in_({"group", "supergroup"}))
        router.callback_query.register(self.on_callback, F.data.startswith("report:"))
        ctx.add_router(router)

    async def submit(self, chat_id: int, reporter_id: int, target_id: int | None, reason: str) -> dict:
        ctx = self.context
        assert ctx is not None
        allowed, retry = await ctx.cache.rate_limit(
            f"report:{ctx.bot_id}:{chat_id}:{reporter_id}",
            limit=1,
            window=int(self.config("rate_limit_minutes", 5)) * 60,
        )
        if not allowed:
            raise ValueError(f"report cooldown active, retry in {retry}s")
        reports = list(await ctx.get_state(f"queue:{chat_id}", []) or [])
        report = {
            "id": f"{chat_id}:{len(reports) + 1}",
            "reporter": reporter_id,
            "target": target_id,
            "reason": reason,
            "status": "open",
        }
        reports.append(report)
        await ctx.set_state(f"queue:{chat_id}", reports[-200:])
        return report

    async def on_report_command(self, message_obj: Message) -> None:
        ctx = self.context
        if ctx is None or message_obj.from_user is None:
            return
        target = message_obj.reply_to_message.from_user.id if message_obj.reply_to_message and message_obj.reply_to_message.from_user else None
        reason = (message_obj.text or "").partition(" ")[2].strip() or "no reason given"
        try:
            report = await self.submit(message_obj.chat.id, message_obj.from_user.id, target, reason)
        except ValueError as exc:
            await ctx.bot.send_message(message_obj.chat.id, f"⚠️ {exc}")
            return
        builder = (
            message("Report received", "flag")
            .key_value("ID", report["id"])
            .key_value("Target", target or "message", "user")
            .key_value("Reporter", user_mention(message_obj.from_user.id, message_obj.from_user.first_name), value_raw=True)
        )
        await ctx.bot.send_message(
            message_obj.chat.id,
            builder.build(),
            reply_markup=keyboard(
                [[Btn("Appeal", callback_data=f"report:appeal:{report['id']}", emoji="mail")]]
            ),
        )

    async def on_callback(self, call: CallbackQuery) -> None:
        ctx = self.context
        if ctx is None:
            return
        parts = (call.data or "").split(":")
        if len(parts) >= 3 and parts[1] == "appeal":
            appeals = list(await ctx.get_state("appeals", []) or [])
            appeals.append({"report": parts[2], "user": call.from_user.id, "status": "pending"})
            await ctx.set_state("appeals", appeals[-200:])
            if call.message:
                await call.message.answer("✅ Appeal submitted for moderator review.")
        await call.answer()


__all__ = ["ReportsPlugin", "MANIFEST"]
