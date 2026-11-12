"""``leech`` handlers — submit, watch, cancel and report mirror/leech jobs.

Commands::

    /leech <url|path> [-unzip] [-zip]   download then upload back to the chat
    /mirror <url|path>                  download into the workspace only
    /tasklist                           your jobs (and the worker's queue)
    /taskstatus <id>                    one job: progress, speed, ETA
    /cancel <id|all>                    stop jobs
    /leechstats                         worker statistics
    /taskbackend                        which backend is wired and whether it answers
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message

from meiko.plugins.base import MeikoPlugin, plugin
from meiko.plugins.context import PluginContext
from meiko.plugins.manifest import PluginManifest
from meiko.plugins.support import OwnCommand, Workspace, human_size
from meiko.ui import Btn, esc, keyboard, message

from .manifest import MANIFEST
from .tasks import (
    DaemonBackend,
    LeechTask,
    LocalBackend,
    TaskManager,
    TaskState,
    guess_kind,
    parse_flags,
)

OWN_COMMAND = OwnCommand(MANIFEST.commands)
MB = 1024 * 1024


def bar(percent: float, width: int = 10) -> str:
    filled = int(round(min(100.0, max(0.0, percent)) / 100 * width))
    return "▰" * filled + "▱" * (width - filled)


def status_line(task: LeechTask) -> str:
    icon = {
        TaskState.QUEUED: "⏳",
        TaskState.ACTIVE: "⬇️",
        TaskState.DONE: "✅",
        TaskState.FAILED: "❌",
        TaskState.CANCELLED: "⛔",
    }[task.state]
    if task.state is TaskState.ACTIVE:
        return f"{icon} {bar(task.percent)} <b>{task.percent:.1f}%</b> · {human_size(int(task.speed))}/s · eta {task.eta}s"
    if task.state is TaskState.DONE:
        return f"{icon} {human_size(task.downloaded)} in {int(task.elapsed)}s"
    if task.state is TaskState.FAILED:
        return f"{icon} {esc(task.error or 'failed')}"
    return f"{icon} {task.state.value}"


@plugin
class LeechPlugin(MeikoPlugin):
    manifest: PluginManifest = MANIFEST

    def __init__(self) -> None:
        super().__init__()
        self._manager: TaskManager | None = None
        self._workspace: Workspace | None = None
        self._jobs: dict[str, asyncio.Task[LeechTask]] = {}

    async def on_start(self, ctx: PluginContext) -> None:
        self._workspace = Workspace(
            MANIFEST.slug,
            scope=ctx.scope.key,
            quota_bytes=int(self.config("max_download_mb", 4096)) * MB,
            max_file_bytes=int(self.config("max_download_mb", 4096)) * MB,
            ttl_seconds=86400,
        )
        self._workspace.ensure()
        backend_name = str(self.config("backend", "local"))
        endpoint = str(self.config("daemon_endpoint", ""))
        backend = LocalBackend() if backend_name == "local" else DaemonBackend(backend_name, endpoint)
        self._manager = TaskManager(
            backend,
            max_active=int(self.config("max_active", 2)),
            max_per_user=int(self.config("max_per_user", 2)),
        )
        router = Router(name="plugin:leech")
        router.message.register(self.on_command, F.text.startswith("/"), OWN_COMMAND)
        router.callback_query.register(self.on_callback, F.data.startswith("lc:"))
        ctx.add_router(router)

    async def on_stop(self, ctx: PluginContext) -> None:
        for task in list(self._jobs.values()):
            if not task.done():
                task.cancel()
        self._jobs.clear()
        self._manager = None
        self._workspace = None

    def manager(self) -> TaskManager:
        if self._manager is None:
            raise RuntimeError("leech plugin is not running")
        return self._manager

    # -- dispatch ----------------------------------------------------------
    async def on_command(self, message_obj: Message, **_: object) -> None:
        text = (message_obj.text or "").strip()
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()
        argument = rest.strip()
        routes = {
            "/leech": self.cmd_leech,
            "/mirror": lambda m, a: self.cmd_leech(m, a or "", force="mirror"),
            "/tasklist": self.cmd_list,
            "/taskstatus": self.cmd_status,
            "/cancel": self.cmd_cancel,
            "/leechstats": self.cmd_stats,
            "/taskbackend": self.cmd_backend,
        }
        handler = routes.get(command)
        if handler is None:
            return
        if command in {"/leech", "/mirror", "/tasklist", "/taskstatus", "/cancel"}:
            await handler(message_obj, argument)
        else:
            await handler(message_obj)

    # -- submission --------------------------------------------------------
    async def cmd_leech(self, message_obj: Message, argument: str, *, force: str = "") -> None:
        manager = self.manager()
        workspace = self._workspace
        assert workspace is not None
        urls, flags = parse_flags(argument)
        if not urls:
            await message_obj.reply(
                message("Mirror & leech", "download")
                .line("<code>/leech https://host/file.zip -unzip</code>", raw=True)
                .line("<code>/mirror /srv/media/movie.mkv</code>", raw=True)
                .line("Flags: -unzip -zip -seed -ytdl -aria2 -qbit -rclone", raw=True)
                .build(),
                reply_markup=keyboard(
                    [
                        [Btn("Backend", callback_data="lc:backend", emoji="server"),
                         Btn("Stats", callback_data="lc:stats", emoji="stats")],
                        [Btn("My jobs", callback_data="lc:list", emoji="loading")],
                    ]
                ),
            )
            return
        ok, reason = manager.can_accept(message_obj.from_user.id)
        if not ok:
            await message_obj.reply(message("Cannot start", "warn").line(esc(reason), raw=True).build())
            return
        task = manager.create(
            urls[0],
            message_obj.from_user.id,
            message_obj.chat.id,
            kind=force or guess_kind(flags),
            flags=flags,
        )
        destination = str(workspace.path_for(Path(urls[0].rstrip("/")).name or f"{task.id}.bin"))
        sent = await message_obj.reply(
            message("Task queued", "download")
            .key_value("ID", task.id, "id")
            .key_value("Target", urls[0][:80], "link")
            .key_value("Kind", task.kind, "gear")
            .line(status_line(task), raw=True)
            .build(),
            reply_markup=keyboard([[Btn("Cancel", callback_data=f"lc:cancel:{task.id}", emoji="cross", style="danger")]]),
        )
        task.message_id = sent.message_id

        async def on_update(updated: LeechTask) -> None:
            await self.edit_text(
                sent,
                message(f"Task · {updated.id}", "download")
                .key_value("Target", updated.url[:80], "link")
                .line(status_line(updated), raw=True)
                .build(),
                reply_markup=keyboard([[Btn("Cancel", callback_data=f"lc:cancel:{updated.id}", emoji="cross", style="danger")]]),
            )

        job = asyncio.ensure_future(
            manager.run(task, destination, on_update=on_update, interval=float(self.config("progress_interval", 4.0)))
        )
        self._jobs[task.id] = job
        try:
            await job
        except asyncio.CancelledError:
            return
        finally:
            self._jobs.pop(task.id, None)

        if task.state is TaskState.DONE and bool(self.config("auto_upload", True)) and task.kind == "leech":
            with contextlib.suppress(Exception):
                await message_obj.reply_document(
                    FSInputFile(destination),
                    caption=f"<code>{esc(Path(destination).name)}</code> · {human_size(task.downloaded)}",
                )
        if not bool(self.config("keep_files", True)):
            with contextlib.suppress(Exception):
                Path(destination).unlink(missing_ok=True)

    # -- reporting ---------------------------------------------------------
    async def cmd_list(self, message_obj: Message, argument: str = "") -> None:
        manager = self.manager()
        user_id = message_obj.from_user.id
        mine = [task for task in manager.tasks.values() if task.user_id == user_id]
        builder = message("Your jobs", "loading")
        if not mine:
            builder.line("No jobs yet — <code>/leech &lt;url&gt;</code>.", raw=True)
        for task in sorted(mine, key=lambda t: t.started_at, reverse=True)[:10]:
            builder.line(f"<code>{task.id}</code> {status_line(task)}", raw=True)
        await message_obj.reply(builder.build(), reply_markup=keyboard([[Btn("Stats", callback_data="lc:stats", emoji="stats")]]))

    async def cmd_status(self, message_obj: Message, argument: str) -> None:
        manager = self.manager()
        task = manager.tasks.get(argument.strip())
        if task is None:
            await message_obj.reply(message("No such task", "search").build())
            return
        await message_obj.reply(
            message(f"Task · {task.id}", "download")
            .key_value("URL", task.url[:80], "link")
            .key_value("State", task.state.value, "gear")
            .key_value("Downloaded", human_size(task.downloaded), "database")
            .key_value("Speed", f"{human_size(int(task.speed))}/s" if task.speed else "—", "rocket")
            .key_value("ETA", f"{task.eta}s" if task.eta else "—", "clock")
            .line(status_line(task), raw=True)
            .build()
        )

    async def cmd_cancel(self, message_obj: Message, argument: str) -> None:
        manager = self.manager()
        target = argument.strip()
        user_id = message_obj.from_user.id
        if target in ("", "all"):
            cancelled = [task.id for task in manager.active_for(user_id) if manager.cancel(task.id, user_id=user_id)]
            await message_obj.reply(
                message("Cancelled", "cross").line(f"{len(cancelled)} job(s) stopped.", raw=True).build()
            )
            return
        if not manager.cancel(target, user_id=user_id):
            await message_obj.reply(message("Cannot cancel", "warn").line("Unknown, finished or not yours.", raw=True).build())
            return
        await message_obj.reply(message("Cancelled", "cross").key_value("ID", target, "id").build())

    async def cmd_stats(self, message_obj: Message) -> None:
        stats = self.manager().stats()
        await message_obj.reply(
            message("Leech stats", "stats")
            .key_value("Backend", stats["backend"], "server")
            .key_value("Ready", "yes" if stats["backend_ready"] else "no", "check" if stats["backend_ready"] else "cross")
            .key_value("Active", str(stats["active"]), "loading")
            .key_value("Queued", str(stats["queued"]), "clock")
            .key_value("Done", str(stats["done"]), "check")
            .key_value("Failed", str(stats["failed"]), "cross")
            .key_value("Transferred", human_size(stats["bytes"]), "database")
            .build()
        )

    async def cmd_backend(self, message_obj: Message, _argument: str = "") -> None:
        manager = self.manager()
        backend = manager.backend
        ready = backend.available()
        builder = message("Transfer backend", "server" if ready else "warn")
        builder.key_value("Backend", backend.name, "server")
        builder.key_value("Reachable", "yes" if ready else "no", "check" if ready else "cross")
        if not ready:
            builder.line(
                "<i>Local copies and http(s) downloads work without a daemon. "
                "Set <code>daemon_endpoint</code> when aria2/qBittorrent/rclone RC is running.</i>",
                raw=True,
            )
        await message_obj.reply(builder.build())

    # -- dashboard integration ---------------------------------------------
    def live_tasks(self) -> list[dict[str, Any]]:
        """The real task list the Transfer Dashboard shows (no synthetic rows)."""
        if self._manager is None:
            return []
        return [self._task_dict(task) for task in self._manager.tasks.values()]

    @staticmethod
    def _task_dict(task: LeechTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "url": task.url,
            "user_id": task.user_id,
            "chat_id": task.chat_id,
            "kind": task.kind,
            "state": task.state.value if hasattr(task.state, "value") else str(task.state),
            "downloaded": task.downloaded,
            "total": task.total,
            "percent": round(task.percent, 1),
            "speed": task.speed,
            "eta": task.eta,
            "destination": task.destination,
            "error": task.error,
            "message_id": task.message_id,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
        }

    async def retry_task(self, task_id: str) -> bool:
        """Re-run a finished task through the same manager and backend."""
        manager = self._manager
        if manager is None:
            return False
        task = manager.tasks.get(task_id)
        if task is None or task.state in (TaskState.QUEUED, TaskState.ACTIVE):
            return False
        task.state = TaskState.QUEUED
        task.downloaded = 0
        task.error = ""
        task.started_at = 0.0
        task.finished_at = 0.0
        workspace = self._workspace
        if workspace is None:
            return False
        destination = str(workspace.path_for(f"retry_{task.id}"))
        job = asyncio.ensure_future(manager.run(task, destination))
        manager._running[task.id] = job  # same bookkeeping the bot uses
        self._jobs[task.id] = job
        return True

    # -- callbacks ---------------------------------------------------------
    async def on_callback(self, call: CallbackQuery) -> None:
        if call.message is None:
            return
        parts = (call.data or "").split(":")
        action = parts[1] if len(parts) > 1 else ""
        if action == "cancel" and len(parts) > 2:
            manager = self.manager()
            ok = manager.cancel(parts[2], user_id=call.from_user.id if call.from_user else None)
            await call.answer("Cancelled" if ok else "Too late")
            if ok:
                with contextlib.suppress(Exception):
                    await call.message.edit_text(message("Cancelled", "cross").key_value("ID", parts[2], "id").build())
            return
        if action == "stats":
            await call.answer()
            await self.cmd_stats(call.message)
            return
        if action == "backend":
            await call.answer()
            await self.cmd_backend(call.message)
            return
        if action == "list":
            await call.answer()
            await self.cmd_list(call.message)
            return
        await call.answer("Unknown control.")


__all__ = ["LeechPlugin", "OWN_COMMAND", "status_line"]
