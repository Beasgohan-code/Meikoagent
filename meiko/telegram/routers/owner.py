"""Owner control center (Telegram side): runtime, plugins, bots, health.

Everything here is guarded by :class:`Principal` checks; hiding a button is
never the only protection.
"""

from __future__ import annotations

import platform
import sys
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from meiko import __version__
from meiko.config import BOT_API_VERSION, get_settings
from meiko.db.base import session_scope
from meiko.db.models import ActivityEvent, BotAccount, BotGroup, PluginInstallation, Worker
from meiko.security.permissions import Principal, Role
from meiko.services.core import DashboardService, PluginService
from meiko.ui import Btn, RichBuilder, keyboard, message

router = Router(name="owner")


def _guard(principal: Principal | None) -> bool:
    return principal is not None and principal.has(Role.BOT_OWNER)


@router.message(Command("owner"))
async def cmd_owner(message: Message, principal: Principal | None = None) -> None:
    if not _guard(principal):
        await message.answer("This command is reserved for the bot owner.")
        return
    await message.answer(owner_panel_text(principal), reply_markup=owner_panel_keyboard(principal))


def owner_panel_text(principal: Principal | None = None) -> str:
    builder = (
        message("Owner control center", "crown")
        .key_value("Platform", f"MEIKO {__version__}", "robot")
        .key_value("Bot API", BOT_API_VERSION, "link")
        .key_value("Runtime", platform.node() or "localhost", "server")
        .key_value("Role", principal.role.name.title() if principal else "—", "shield")
    )
    with_buttons = builder.build()
    return with_buttons


def owner_panel_keyboard(principal: Principal | None = None):
    rows = [
        [
            Btn("Runtime", callback_data="owner:runtime", emoji="server", style="primary"),
            Btn("Health", callback_data="owner:health", emoji="stats"),
        ],
        [
            Btn("Plugins", callback_data="owner:plugins", emoji="plug"),
            Btn("Bots", callback_data="owner:bots", emoji="robot"),
        ],
        [Btn("Security", callback_data="owner:security", emoji="lock")],
    ]
    if principal is not None and principal.is_platform_owner:
        rows.append([Btn("Platform admin", callback_data="owner:platform", emoji="crown", style="danger")])
    return keyboard(rows)


@router.callback_query(F.data == "owner:panel")
async def cb_panel(call: CallbackQuery, principal: Principal | None = None) -> None:
    if not _guard(principal):
        await call.answer("Owner only", show_alert=True)
        return
    if call.message:
        await call.message.edit_text(
            owner_panel_text(principal), reply_markup=owner_panel_keyboard(principal)
        )


@router.callback_query(F.data == "owner:runtime")
async def cb_runtime(call: CallbackQuery, principal: Principal | None = None, meiko=None) -> None:
    if not _guard(principal):
        await call.answer("Owner only", show_alert=True)
        return
    builder = message("Runtime", "server")
    if meiko is not None:
        uptime = int(time.time() - meiko.started_at)
        builder.key_value("Dispatcher", meiko.dispatcher_id, "code")
        builder.key_value("Uptime", f"{uptime // 3600}h {(uptime % 3600) // 60}m", "clock")
        builder.key_value("Updates", meiko.counters.get("updates", 0), "graph")
    builder.key_value("Python", sys.version.split()[0], "code")
    builder.key_value("Node", platform.node(), "server")
    if call.message:
        await call.message.edit_text(
            builder.build(), reply_markup=keyboard([[Btn("Back", callback_data="owner:panel", emoji="arrow")]])
        )


@router.callback_query(F.data == "owner:health")
async def cb_health(call: CallbackQuery, principal: Principal | None = None) -> None:
    if not _guard(principal):
        await call.answer("Owner only", show_alert=True)
        return
    from meiko.cache import get_cache
    from meiko.db.base import database_health

    db = await database_health()
    cache = await get_cache().ping()
    async with session_scope() as session:
        bots = int(await session.scalar(select(func.count()).select_from(BotAccount)) or 0)
        running = int(
            await session.scalar(
                select(func.count()).select_from(BotAccount).where(BotAccount.status == "running")
            )
            or 0
        )
        groups = int(await session.scalar(select(func.count()).select_from(BotGroup)) or 0)
        events = int(await session.scalar(select(func.count()).select_from(ActivityEvent)) or 0)
    rich = (
        RichBuilder()
        .heading("Platform health", 2)
        .table(
            [
                ["Component", "State"],
                ["Database", db.get("status", "?")],
                ["Cache", cache.get("status", "?")],
                ["Bots", f"{running}/{bots} running"],
                ["Groups", str(groups)],
                ["Events", str(events)],
            ]
        )
        .paragraph("Health is computed from live probes, never a static 200.")
    )
    if call.message:
        await call.message.edit_text(
            message("Health", "stats")
            .key_value("Database", db.get("status", "?"), "db")
            .key_value("Cache", cache.get("status", "?"), "queue")
            .key_value("Bots running", f"{running}/{bots}", "robot")
            .key_value("Groups", groups, "users")
            .build(),
            reply_markup=keyboard([[Btn("Back", callback_data="owner:panel", emoji="arrow")]]),
        )


@router.callback_query(F.data == "owner:plugins")
async def cb_plugins(call: CallbackQuery, principal: Principal | None = None, meiko=None) -> None:
    if not _guard(principal) or meiko is None:
        await call.answer("Owner only", show_alert=True)
        return
    async with session_scope() as session:
        installs = await PluginService(session).installations_for_bot(meiko.bot_id)
        rows_by_id = {i.plugin_id: i for i in installs}
        plugins = await PluginService(session).catalog()
        lines = []
        for plugin in plugins:
            install = rows_by_id.get(plugin.id)
            state = install.state if install else "available"
            lines.append((plugin.icon, plugin.name, state))
    builder = message("Plugins for this bot", "plug")
    for icon, name, state in lines:
        builder.line(f"{icon} <b>{name}</b> — <code>{state}</code>", raw=True)
    if call.message:
        await call.message.edit_text(
            builder.build(), reply_markup=keyboard([[Btn("Back", callback_data="owner:panel", emoji="arrow")]])
        )


@router.callback_query(F.data == "owner:bots")
async def cb_bots(call: CallbackQuery, principal: Principal | None = None) -> None:
    if not _guard(principal):
        await call.answer("Owner only", show_alert=True)
        return
    async with session_scope() as session:
        bots = (
            await session.scalars(
                select(BotAccount).where(BotAccount.owner_id == principal.user_id).limit(20)
            )
        ).all()
    builder = message("Your bots", "robot")
    if not bots:
        builder.line("No bots connected yet.")
    for bot in bots:
        builder.line(
            f"{'🤖'} <b>{bot.name}</b> @{bot.username or '—'} — <code>{bot.status}</code>"
            + (" (clone)" if bot.is_clone else ""),
            raw=True,
        )
    if call.message:
        await call.message.edit_text(
            builder.build(),
            reply_markup=keyboard(
                [
                    [Btn("Manage", url="https://meiko.agent/dashboard", emoji="arrow")],
                    [Btn("Back", callback_data="owner:panel", emoji="arrow")],
                ]
            ),
        )


@router.callback_query(F.data == "owner:security")
async def cb_security(call: CallbackQuery, principal: Principal | None = None) -> None:
    if not _guard(principal):
        await call.answer("Owner only", show_alert=True)
        return
    settings = get_settings()
    builder = (
        message("Security posture", "lock")
        .key_value("Environment", settings.environment.upper(), "server")
        .key_value("Token encryption", "Fernet (AES-128-CBC + HMAC)", "key")
        .key_value("Session TTL", f"{settings.session_ttl_seconds // 86400} days", "clock")
        .key_value("Dev login", "enabled" if settings.auth_dev_mode else "disabled", "warn")
        .line("")
        .line("<i>Secrets are never rendered in Telegram or the webapp.</i>", raw=True)
    )
    if call.message:
        await call.message.edit_text(
            builder.build(), reply_markup=keyboard([[Btn("Back", callback_data="owner:panel", emoji="arrow")]])
        )


@router.callback_query(F.data == "owner:platform")
async def cb_platform(call: CallbackQuery, principal: Principal | None = None) -> None:
    if principal is None or not principal.is_platform_owner:
        await call.answer("Platform owner only", show_alert=True)
        return
    async with session_scope() as session:
        bots = int(await session.scalar(select(func.count()).select_from(BotAccount)) or 0)
        workers = (
            await session.scalars(select(Worker).order_by(Worker.id.desc()).limit(5))
        ).all()
    builder = message("Platform administration", "crown").key_value("Total bots", bots, "robot")
    for worker in workers:
        builder.key_value(
            f"Worker {worker.name}", f"{worker.status} ({worker.current_load}/{worker.capacity})", "server"
        )
    if call.message:
        await call.message.edit_text(
            builder.build(), reply_markup=keyboard([[Btn("Back", callback_data="owner:panel", emoji="arrow")]])
        )
