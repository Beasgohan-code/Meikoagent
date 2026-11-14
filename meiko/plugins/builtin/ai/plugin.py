"""AI plugin – provider-agnostic bridge.

Rules:
* the provider URL/model is owner configuration, never hardcoded secrets,
* when no API key is configured the plugin reports "not configured" instead of
  fabricating an answer,
* outbound HTTP is an explicit permission (``http.outbound``).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.ui import message

from .manifest import MANIFEST


@plugin
class AIPlugin(MeikoPlugin):
    manifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        router = Router(name="plugin:ai")
        router.message.register(self.on_message, F.text.startswith("/ask"))
        ctx.add_router(router)

    async def ask(self, prompt: str) -> str:
        ctx = self.context
        assert ctx is not None
        ctx.require("http.outbound")
        api_key = str(self.config("api_key", "") or "")
        if not api_key:
            return "AI is not configured yet – the owner must add an API key."
        import httpx

        base = str(self.config("api_base", "https://api.openai.com/v1")).rstrip("/")
        payload = {
            "model": self.config("model", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": self.config("system_prompt", "")},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": int(self.config("max_tokens", 400)),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{base}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if response.status_code != 200:
            ctx.log("error", "ai provider error", status=response.status_code)
            return f"AI provider error ({response.status_code})."
        data = response.json()
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError):
            return "AI provider returned an unexpected payload."

    async def on_message(self, message_obj: Message) -> None:
        ctx = self.context
        if ctx is None or not message_obj.text:
            return
        prompt = message_obj.text.partition(" ")[2].strip()
        if not prompt:
            await ctx.bot.send_message(message_obj.chat.id, "Usage: <code>/ask your question</code>")
            return
        answer = await self.ask(prompt)
        await ctx.bot.send_message(
            message_obj.chat.id, message("MEIKO AI", "orb").line(answer).build()
        )


__all__ = ["AIPlugin", "MANIFEST"]
