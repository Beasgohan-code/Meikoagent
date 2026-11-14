"""Analytics plugin: usage counters, activity digests and leaderboards."""

from __future__ import annotations

from collections import Counter

from aiogram import F, Router
from aiogram.types import Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.ui import Btn, RichBuilder, keyboard, message

from .manifest import MANIFEST


@plugin
class AnalyticsPlugin(MeikoPlugin):
    manifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        router = Router(name="plugin:analytics")
        router.message.register(self.on_message, F.chat.type.in_({"group", "supergroup"}))
        ctx.add_router(router)
        ctx.schedule("digest", 3600, self.publish_digest)

    async def bump(self, chat_id: int, user_id: int, field: str) -> int:
        ctx = self.context
        assert ctx is not None
        key = f"daily:{chat_id}:{user_id}"
        data = dict(await ctx.get_state(key, {}) or {})
        data[field] = int(data.get(field, 0)) + 1
        await ctx.set_state(key, data, ttl=86_400 * 2)
        return data[field]

    async def on_message(self, message_obj: Message) -> None:
        ctx = self.context
        if ctx is None or message_obj.from_user is None:
            return
        await self.bump(message_obj.chat.id, message_obj.from_user.id, "messages")
        totals = dict(await ctx.get_state(f"totals:{message_obj.chat.id}", {}) or {})
        totals["messages"] = int(totals.get("messages", 0)) + 1
        await ctx.set_state(f"totals:{message_obj.chat.id}", totals)

    async def leaderboard(self, chat_id: int) -> list[tuple[int, int]]:
        ctx = self.context
        assert ctx is not None
        board: Counter[int] = Counter()
        for key in await self.scan_keys(f"daily:{chat_id}:"):
            user_id = int(key.rsplit(":", 1)[-1])
            data = await ctx.get_state(key, {}) or {}
            board[user_id] += int(data.get("messages", 0))
        return board.most_common(int(self.config("top_size", 10)))

    async def scan_keys(self, prefix: str) -> list[str]:
        """List keys of this plugin's storage matching ``prefix``.

        Returns *plugin-relative* keys (``daily:<chat>:<user>``) so they can be
        fed straight back into ``ctx.get_state``.
        """
        ctx = self.context
        assert ctx is not None
        backend = ctx.cache.backend
        # plugin storage is namespaced: ``<cache>:plugin:<slug>:<scope>:<key>``
        store = getattr(backend, "_data", None)
        namespace = ctx.cache.key(ctx.storage_namespace, prefix.rstrip(":")) + ":"
        strip = len(ctx.cache.key(ctx.storage_namespace)) + 1
        if store is not None:  # in-memory fallback
            return [k[strip:] for k in list(store) if k.startswith(namespace)]
        client = getattr(backend, "client", None)
        if client is not None:  # pragma: no cover - requires redis
            keys = []
            async for raw in client.scan_iter(match=namespace + "*", count=200):
                key = raw.decode() if isinstance(raw, bytes) else raw
                keys.append(key[strip:])
            return keys
        return []

    async def publish_digest(self) -> None:
        ctx = self.context
        if ctx is None or ctx._bot is None:
            return
        totals = dict(await ctx.get_state("subscribed_chats", {}) or {})
        for chat_id in list(totals)[:10]:
            board = await self.leaderboard(int(chat_id))
            rich = RichBuilder().heading("Daily digest", 2)
            if board:
                rich.table(
                    [["Rank", "Member", "Messages"]]
                    + [[str(i + 1), str(uid), str(count)] for i, (uid, count) in enumerate(board)]
                )
            else:
                rich.paragraph("No activity recorded yet today.")
            try:
                await ctx.bot.send_rich_message(
                    chat_id=int(chat_id), rich_message=rich.build()
                )
            except Exception as exc:  # pragma: no cover - network
                ctx.log("warning", "digest delivery failed", chat=chat_id, error=str(exc))

    async def render_summary(self, chat_id: int) -> str:
        ctx = self.context
        assert ctx is not None
        totals = dict(await ctx.get_state(f"totals:{chat_id}", {}) or {})
        board = await self.leaderboard(chat_id)
        builder = (
            message("Group analytics", "analytics")
            .key_value("Messages", totals.get("messages", 0), "message")
            .key_value("Tracked members", len(board), "users")
        )
        if board:
            builder.table(
                ["Member", "Messages"], [[str(uid), str(count)] for uid, count in board[:5]]
            )
        return builder.build()

    def summary_keyboard(self) -> object:
        return keyboard([[Btn("Refresh", callback_data="analytics:refresh", emoji="arrow", style="primary")]])


__all__ = ["AnalyticsPlugin", "MANIFEST"]
