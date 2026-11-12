"""``code_runner`` handlers: double-gated, quota-limited snippet execution."""

from __future__ import annotations

import shlex
from datetime import date

from aiogram import F, Router
from aiogram.types import Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.support import CapabilityGate, GateDenied, scope_owner
from meiko.ui import Btn, duration, esc, keyboard, message

from .manifest import MANIFEST
from .runtime import (
    ALLOWED_LANGUAGES,
    RunLimits,
    available_languages,
    language_label,
    run_snippet,
)

CAPABILITY = "code_exec"
USAGE = (
    "/py &lt;code&gt;            run a Python snippet\n"
    "/run &lt;lang&gt; &lt;code&gt;     run python | node | bash\n"
    "/sandbox               show limits and remaining runs"
)


@plugin
class CodeRunnerPlugin(MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    async def on_start(self, ctx: PluginContext) -> None:
        router = Router(name="plugin:code_runner")
        router.message.register(self.on_message, F.chat.type.in_({"private", "group", "supergroup"}))
        ctx.add_router(router)

    # -- helpers ---------------------------------------------------------
    def limits(self) -> RunLimits:
        return RunLimits(
            timeout_seconds=int(self.config("timeout_seconds", 10)),
            max_output_chars=int(self.config("max_output_chars", 3500)),
            max_source_chars=int(self.config("max_source_chars", 4000)),
            memory_mb=int(self.config("memory_mb", 256)),
            cpu_seconds=int(self.config("cpu_seconds", 5)),
            allow_network=bool(self.config("allow_network", False)),
            allow_file_write=bool(self.config("allow_file_write", False)),
        )

    async def _context_owner(self) -> tuple[int | None, bool]:
        """Return (bot owner telegram id, platform owner flag) for the scope."""
        ctx = self.context
        assert ctx is not None
        return await scope_owner(ctx)

    async def _authorise(self, message_obj: Message) -> bool:
        ctx = self.context
        assert ctx is not None
        user = message_obj.from_user
        if user is None:
            return False
        owner_telegram_id, platform_owner = await self._context_owner()
        gate = CapabilityGate(CAPABILITY)
        try:
            await gate.require(
                telegram_id=user.id,
                bot_id=ctx.bot_id,
                platform_owner=platform_owner,
                owner_telegram_id=owner_telegram_id,
            )
        except GateDenied as exc:
            await message_obj.reply(
                message("Code execution is closed", "lock")
                .line(exc.reason)
                .blank()
                .line("A platform owner can open it with /owner → capabilities.", raw=True)
                .build()
            )
            return False
        if not bool(self.config("enabled", False)):
            await message_obj.reply(
                message("Code execution is disabled for this installation", "lock")
                .line("Enable it in the plugin configuration first.", raw=True)
                .build()
            )
            return False
        return True

    async def _consume_quota(self, user_id: int) -> tuple[int, int]:
        ctx = self.context
        assert ctx is not None
        daily = int(self.config("daily_runs", 50))
        today = date.today().isoformat()
        key = f"runs:{today}:{user_id}"
        used = int(await ctx.get_state(key, 0) or 0)
        if daily and used >= daily:
            return used, daily
        await ctx.set_state(key, used + 1, ttl=60 * 60 * 26)
        return used + 1, daily

    # -- handlers --------------------------------------------------------
    async def on_message(self, message_obj: Message) -> None:
        text = (message_obj.text or "").strip()
        if not text.startswith("/"):
            return
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()

        if command == "/sandbox":
            await self.show_sandbox(message_obj)
            return
        if command not in ("/py", "/run", "/runlang"):
            return
        if not await self._authorise(message_obj):
            return

        languages = [str(item) for item in (self.config("languages", ["python"]) or [])]
        if command == "/py":
            language, source = "python", rest
        else:
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                await message_obj.reply("Usage: /run &lt;language&gt; &lt;code&gt;")
                return
            language, source = parts[0].lower(), parts[1]

        if language not in ALLOWED_LANGUAGES:
            await message_obj.reply(f"Unknown language <b>{esc(language)}</b>. Try: {', '.join(languages)}")
            return
        if languages and language not in languages:
            await message_obj.reply(
                f"<b>{esc(language_label(language))}</b> is not enabled for this installation."
            )
            return

        used, daily = await self._consume_quota(message_obj.from_user.id)
        if daily and used > daily:
            await message_obj.reply(
                message("Daily quota reached", "clock")
                .line(f"Limit: {daily} runs per day per user.", raw=True)
                .build()
            )
            return

        result = await run_snippet(source, language=language, limits=self.limits())
        await message_obj.reply(
            render_result(result, source if bool(self.config("echo_source", True)) else ""),
            reply_markup=self._keyboard(),
        )

        ctx = self.context
        assert ctx is not None
        ctx.log(
            "info",
            "snippet executed",
            user=message_obj.from_user.id,
            language=result.language,
            ok=result.ok,
            duration_ms=result.duration_ms,
        )
        if bool(self.config("notify_owner", True)):
            owner_telegram_id, _ = await self._context_owner()
            if owner_telegram_id and owner_telegram_id != message_obj.from_user.id:
                try:
                    await ctx.bot.send_message(
                        owner_telegram_id,
                        message("Code executed", "code")
                        .key_value("By", f"{message_obj.from_user.id}", "user")
                        .key_value("Language", result.language, "code")
                        .key_value("Status", "ok" if result.ok else "failed", "check")
                        .key_value("Duration", f"{result.duration_ms} ms", "clock")
                        .build(),
                    )
                except Exception:  # pragma: no cover - owner may block the bot
                    ctx.log("warning", "could not notify owner about snippet run")

    async def show_sandbox(self, message_obj: Message) -> None:
        limits = self.limits()
        languages = available_languages()
        allowed = [str(item) for item in (self.config("languages", languages) or [])]
        builder = (
            message("Sandbox", "shield")
            .key_value("Gate", "open" if CapabilityGate else "unknown", "lock")
            .key_value("Timeout", duration(limits.timeout_seconds), "clock")
            .key_value("Memory cap", f"{limits.memory_mb} MB", "server")
            .key_value("CPU cap", duration(limits.cpu_seconds), "gear")
            .key_value("Output cap", f"{limits.max_output_chars} chars", "code")
            .key_value("Network", "allowed" if limits.allow_network else "blocked", "wifi")
            .key_value("File writes", "allowed" if limits.allow_file_write else "blocked", "lock")
            .blank()
            .line(f"<b>Languages</b>: {', '.join(l for l in allowed if l in languages) or 'none'}", raw=True)
            .line(f"<b>Daily runs</b>: {int(self.config('daily_runs', 50))} per user", raw=True)
        )
        await message_obj.reply(builder.build(), reply_markup=self._keyboard())

    def _keyboard(self):
        return keyboard(
            [
                [
                    Btn("Usage", callback_data="code:usage", emoji="book"),
                    Btn("Sandbox", callback_data="code:sandbox", emoji="shield"),
                ]
            ]
        )


def render_result(result, echoed_source: str = "") -> str:
    """Render a :class:`RunResult` as Telegram HTML (single place, testable)."""
    builder = message("Code Runner", "code" if result.ok else "cross")
    if echoed_source:
        snippet = echoed_source.strip()
        builder.code(snippet[:600] + ("…" if len(snippet) > 600 else ""), result.language)
    builder.key_value("Language", language_label(result.language), "code")
    builder.key_value("Status", "ok" if result.ok else "failed", "check" if result.ok else "cross")
    builder.key_value("Duration", f"{result.duration_ms} ms", "clock")
    if result.timed_out:
        builder.line(f"<b>Timed out</b> after {result.reason}", raw=True)
    if result.stdout:
        builder.blank().line("<b>stdout</b>", raw=True).code(result.stdout)
    if result.stderr:
        builder.blank().line("<b>stderr</b>", raw=True).code(result.stderr)
    if not result.ok and not result.stderr:
        builder.line(esc(result.reason or "execution failed"), raw=True)
    if result.truncated:
        builder.blank().line("<i>Output truncated to the configured cap.</i>", raw=True)
    return builder.build()


__all__ = ["CodeRunnerPlugin", "MANIFEST", "render_result", "USAGE", "shlex"]
