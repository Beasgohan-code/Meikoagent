"""``keep_alive`` handlers and the self-ping job."""

from __future__ import annotations

import asyncio
import time

from aiogram import F, Router
from aiogram.types import Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.support import (
    OwnCommand,
    PluginState,
    scope_owner,
)
from meiko.ui import esc, message

from .manifest import MANIFEST
from .watchdog import HeartbeatTracker, format_duration, next_ping_due, utcnow

STATE_KEY = "heartbeat:state"


#: Only dispatch commands this package declares – never the whole "/" space.
OWN_COMMAND = OwnCommand([*MANIFEST.commands, "ping", "heartbeat"])

@plugin
class KeepAlivePlugin(MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._tracker = HeartbeatTracker()
        self._state: PluginState | None = None
        #: Off by default: pinging to reset an idle timer is what gets a bot
        #: suspended by its host.  An owner may enable it on a host they run.
        self._enabled = False
        self._owner_id: int | None = None
        self._bot_username: str = ""

    async def on_start(self, ctx: PluginContext) -> None:
        self._state = PluginState(ctx)
        self._tracker = HeartbeatTracker.from_dict(
            await self._state.get(STATE_KEY), history=int(self.config("history", 50))
        )
        self._owner_id, _ = await scope_owner(ctx)
        router = Router(name="plugin:keep_alive")
        router.message.register(self.on_command, F.text.startswith("/"), OWN_COMMAND)
        ctx.add_router(router)
        ctx.schedule(
            "heartbeat",
            int(self.config("interval_seconds", 300)),
            self.self_ping,
            # No longer runs immediately: the job now only reports, and a burst of
            # calls at boot is noise on a restarting free-tier service.
            run_immediately=False,
        )

    async def on_stop(self, ctx: PluginContext) -> None:
        await self.persist()

    async def persist(self) -> None:
        ctx = self.context
        if ctx is not None:
            state = self._state or PluginState(ctx)
            await state.set(STATE_KEY, self._tracker.to_dict())

    # -- commands --------------------------------------------------------
    async def on_command(self, message_obj: Message, **_: object) -> None:
        text = (message_obj.text or "").strip()
        command = text.split()[0].split("@")[0].lower()
        if command in ("/ping", "/latency", "/uptime"):
            await self.cmd_ping(message_obj)
        elif command == "/heartbeat":
            await self.cmd_status(message_obj)
        elif command == "/keepalive":
            await self.cmd_panel(message_obj)
        elif command in ("/keepaliveon", "/keepaliveoff"):
            self._enabled = command.endswith("on")
            await message_obj.reply(
                f"Keep-alive <b>{'enabled' if self._enabled else 'paused'}</b>."
            )

    async def cmd_ping(self, message_obj: Message) -> None:
        ctx = self.context
        assert ctx is not None
        started = time.perf_counter()
        try:
            sent = await ctx.bot.send_message(message_obj.chat.id, "🏓")
            latency = int((time.perf_counter() - started) * 1000)
            self._tracker.record(latency, True)
            await self.edit_text(
                sent,
                message("Pong", "wifi")
                .key_value("Latency", f"{latency} ms", "clock")
                .key_value("Uptime", format_duration(self._tracker.state.uptime.total_seconds()), "clock")
                .build(),
            )
        except Exception as exc:  # pragma: no cover - network failures
            self._tracker.record(0, False, str(exc))
            await message_obj.reply(f"Ping failed: {esc(str(exc))}")

    async def cmd_status(self, message_obj: Message) -> None:
        builder = message("Heartbeat", "heartbeat")
        for label, value in self._tracker.summary_rows(int(self.config("failure_threshold", 3))):
            builder.key_value(label, value)
        await message_obj.reply(builder.build())

    async def cmd_panel(self, message_obj: Message) -> None:
        builder = (
            message("Keep alive", "heartbeat")
            .key_value("Enabled", "yes" if self._enabled else "paused", "check")
            .key_value("Interval", f"{self.config('interval_seconds', 300)}s", "clock")
            .key_value("Alert after", f"{self.config('failure_threshold', 3)} failures", "warn")
        )
        for label, value in self._tracker.summary_rows(int(self.config("failure_threshold", 3)))[:4]:
            builder.key_value(label, value)
        await message_obj.reply(builder.build())

    # -- job -------------------------------------------------------------
    async def self_ping(self) -> None:
        """Measure API reachability; keeps the *reporting* honest, nothing else.

        Nothing here is meant to keep an idle service awake - if it were, it would
        be circumventing the host's idle policy, which is grounds for suspension.
        """
        ctx = self.context
        if ctx is None or not self._enabled:
            return
        interval = int(self.config("interval_seconds", 300))
        if not next_ping_due(self._tracker.state.last_ping_at, interval):
            return
        started = time.perf_counter()
        ok = True
        detail = ""
        try:
            await asyncio.wait_for(
                ctx.bot.get_me(), timeout=float(self.config("ping_timeout_seconds", 10))
            )
        except Exception as exc:  # pragma: no cover - network failures
            ok = False
            detail = str(exc)
        latency = int((time.perf_counter() - started) * 1000)
        alert = self._tracker.record(
            latency, ok, detail, failure_threshold=int(self.config("failure_threshold", 3))
        )
        ctx.log("debug", f"keep-alive ping ok={ok} latency={latency}ms")
        if alert:
            await self.announce(alert, detail)
        await self.persist()

    async def announce(self, alert: str, detail: str = "") -> None:
        if not bool(self.config("notify_owner", True)):
            return
        ctx = self.context
        if ctx is None:
            return
        owner = self._owner_id
        if owner is None:
            ctx.log("info", f"keep-alive alert {alert!r} (no owner id available)")
            return
        if self.in_quiet_hours():
            ctx.log("info", f"keep-alive alert {alert!r} suppressed (quiet hours)")
            return
        if alert == "down":
            text = (
                message("Connection lost", "warn")
                .key_value("Failures", str(self._tracker.state.consecutive_failures), "warn")
                .key_value("Detail", detail[:120] or "no response", "shield")
                .line("<i>The bot keeps retrying automatically.</i>", raw=True)
                .build()
            )
        else:
            text = (
                message("Back online", "check")
                .key_value("Latency", f"{self._tracker.latency_summary()['avg']} ms avg", "clock")
                .key_value("Uptime", format_duration(self._tracker.state.uptime.total_seconds()), "clock")
                .build()
            )
        try:
            await ctx.bot.send_message(owner, text)
        except Exception as exc:  # pragma: no cover - owner blocked the bot
            ctx.log("warning", f"keep-alive alert not delivered: {exc}")
        extra = str(self.config("notify_chat", "") or "").strip()
        if extra:
            try:
                await ctx.bot.send_message(extra, text)
            except Exception:  # pragma: no cover - optional chat
                pass

    def in_quiet_hours(self) -> bool:
        if not bool(self.config("quiet_hours", False)):
            return False
        return utcnow().hour < 6


__all__ = ["KeepAlivePlugin", "MANIFEST"]
