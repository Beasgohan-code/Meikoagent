"""Developer plugin: introspection helpers for bot owners."""

from __future__ import annotations

import platform
import sys

from aiogram import F, Router
from aiogram.types import Message

from meiko import __version__
from meiko.config import BOT_API_VERSION
from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.ui import RichBuilder, message

MANIFEST = PluginManifest(
    slug="developer",
    name="Developer Tools",
    version="1.0.0",
    description="Runtime introspection: version, Bot API coverage, plugin health, ids.",
    category="developer",
    icon="code",
    permissions=["messages.send", "storage.read"],
    commands=["dev", "runtime", "coverage"],
    events=["message"],
    config_schema={
        "show_tracebacks": {"type": "bool", "default": False, "label": "Expose tracebacks"},
    },
)


@plugin
class DeveloperPlugin(MeikoPlugin):
    manifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        router = Router(name="plugin:developer")
        router.message.register(self.on_message, F.text.startswith("/dev"))
        ctx.add_router(router)

    def runtime_info(self) -> dict[str, str]:
        return {
            "meiko": __version__,
            "bot_api": BOT_API_VERSION,
            "python": sys.version.split()[0],
            "platform": platform.system(),
        }

    async def on_message(self, message_obj: Message) -> None:
        ctx = self.context
        if ctx is None:
            return
        info = self.runtime_info()
        builder = (
            message("Developer runtime", "code")
            .key_value("MEIKO", info["meiko"], "robot")
            .key_value("Bot API", info["bot_api"], "link")
            .key_value("Python", info["python"], "code")
            .key_value("Platform", info["platform"], "server")
        )
        rich = (
            RichBuilder()
            .heading("Plugin health", 2)
            .table([["Scope", "State"], [ctx.scope.key, "running"]])
            .paragraph("Bot API 10.4: widgets are handled by the Meiko typed adapter.")
        )
        try:
            await ctx.bot.send_rich_message(
                message_obj.chat.id, rich_message=rich.build(), reply_to_message_id=message_obj.message_id
            )
        except Exception:
            await ctx.bot.send_message(message_obj.chat.id, builder.build())


__all__ = ["DeveloperPlugin", "MANIFEST"]
