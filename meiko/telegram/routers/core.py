"""Core router: /start, /help, /menu, /about, /status – rich, premium-emoji UI."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from meiko.config import BOT_API_VERSION
from meiko.core.commands import registry as commands
from meiko.security.permissions import Principal, Role
from meiko.ui import (
    Btn,
    MessageBuilder,
    RichBuilder,
    keyboard,
    message,
    p,
)

router = Router(name="core")


def _is_private(message: Message) -> bool:
    return message.chat.type == "private"


@router.message(CommandStart())
async def cmd_start(message: Message, principal: Principal | None = None) -> None:
    name = message.from_user.first_name if message.from_user else "there"
    builder = (
        message(f"Hello, {name}", "robot")
        .subtitle("MEIKO AGENT – your Telegram control plane for moderation, plugins and automation.")
        .blank()
        .key_value("Bot API", BOT_API_VERSION, "link")
        .key_value("Your role", (principal.role.name if principal else "MEMBER").title(), "shield")
        .blank()
        .line("Use the menu below to explore, or /help for the full command list.", raw=False)
    )
    markup = keyboard(
        [
            [
                Btn("Help", callback_data="core:help", emoji="book", style="primary"),
                Btn("Status", callback_data="core:status", emoji="stats"),
            ],
            [
                Btn("Moderation", callback_data="core:mod", emoji="gavel"),
                Btn("Plugins", callback_data="core:plugins", emoji="plug"),
            ],
        ]
    )
    await message.answer(builder.build(), reply_markup=markup)


@router.message(Command("help"))
async def cmd_help(message: Message, principal: Principal | None = None) -> None:
    await message.answer(render_help(principal), reply_markup=help_keyboard(principal))


def render_help(principal: Principal | None = None) -> str:
    categories: dict[str, list] = {}
    for cmd in commands.unique():
        if cmd.hidden:
            continue
        if principal is not None and not principal.has(cmd.permission):
            continue
        categories.setdefault(cmd.category, []).append(cmd)
    builder = message("Command reference", "book")
    if not categories:
        builder.line("No commands are registered for your role yet.")
        return builder.build()
    for category in sorted(categories):
        builder.blank()
        builder.line(f"<b>{category.title()}</b>", raw=True)
        for cmd in sorted(categories[category], key=lambda c: c.name):
            aliases = f" ({', '.join('/' + a for a in cmd.aliases)})" if cmd.aliases else ""
            builder.line(f"/{cmd.name}{aliases} – {cmd.description}")
    builder.blank()
    builder.line("<i>Commands are registered by core and by enabled plugins only.</i>", raw=True)
    return builder.build()


def help_keyboard(principal: Principal | None = None):
    rows = [[Btn("Close", callback_data="core:close", emoji="cross", style="danger")]]
    if principal is not None and principal.has(Role.BOT_OWNER):
        rows.insert(0, [Btn("Owner panel", callback_data="owner:panel", emoji="crown", style="primary")])
    return keyboard(rows)


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    builder = (
        message("Main menu", "hex")
        .bullet("Moderation – warn, mute, ban, purge", "gavel")
        .bullet("Anti-Spam – flood, links, invites, keywords", "shield")
        .bullet("Plugins – install, configure, inspect logs", "plug")
        .bullet("Automation – workflows and schedules", "gear")
        .bullet("Analytics – activity, leaderboards, digests", "stats")
    )
    await message.answer(
        builder.build(),
        reply_markup=keyboard(
            [
                [Btn("Moderation", callback_data="core:mod", emoji="gavel")],
                [Btn("Plugins", callback_data="core:plugins", emoji="plug")],
                [Btn("Status", callback_data="core:status", emoji="stats")],
            ]
        ),
    )


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    from meiko import __version__

    rich = (
        RichBuilder()
        .heading("MEIKO AGENT", 1)
        .paragraph("Unified Telegram ecosystem: moderation, plugins, multi-bot hosting and a SaaS control plane.")
        .table(
            [
                ["Component", "Version"],
                ["Platform", __version__],
                ["Bot API", BOT_API_VERSION],
            ]
        )
        .footer("Built with aiogram, FastAPI, SQLAlchemy 2 and Next.js")
    )
    try:
        await message.answer_rich(rich.build())
    except Exception:
        await message.answer(
            message("MEIKO AGENT", "robot")
            .key_value("Platform", __version__, "robot")
            .key_value("Bot API", BOT_API_VERSION, "link")
            .build()
        )


@router.message(Command("status"))
async def cmd_status(message: Message, meiko=None, principal: Principal | None = None) -> None:
    builder = message("Runtime status", "stats")
    if meiko is not None:
        builder.key_value("Runtime", f"{meiko.dispatcher_id}", "server")
        builder.key_value("Updates handled", meiko.counters.get("updates", 0), "graph")
    builder.key_value("Registered commands", len(commands), "code")
    if principal is not None:
        builder.key_value("Role", principal.role.name.title(), "shield")
    await message.answer(builder.build())


@router.callback_query(F.data == "core:help")
async def cb_help(call: CallbackQuery, principal: Principal | None = None) -> None:
    if call.message:
        await call.message.edit_text(render_help(principal), reply_markup=help_keyboard(principal))


@router.callback_query(F.data == "core:status")
async def cb_status(call: CallbackQuery, meiko=None) -> None:
    builder = message("Runtime status", "stats")
    if meiko is not None:
        builder.key_value("Updates handled", meiko.counters.get("updates", 0), "graph")
        builder.key_value("Bot", meiko.bot_username or meiko.bot_id, "robot")
    builder.key_value("Commands", len(commands), "code")
    if call.message:
        await call.message.edit_text(
            builder.build(),
            reply_markup=keyboard([[Btn("Back", callback_data="core:help", emoji="arrow")]]),
        )


@router.callback_query(F.data == "core:plugins")
async def cb_plugins(call: CallbackQuery, principal: Principal | None = None) -> None:
    from meiko.plugins.base import registry

    manifests = registry.manifests()
    builder = message("Plugin catalogue", "plug")
    for manifest in sorted(manifests, key=lambda m: m.category):
        builder.bullet(f"{manifest.name} v{manifest.version} – {manifest.category}", manifest.icon)
    if call.message:
        await call.message.edit_text(
            builder.build(),
            reply_markup=keyboard([[Btn("Back", callback_data="core:help", emoji="arrow")]]),
        )


@router.callback_query(F.data == "core:mod")
async def cb_mod(call: CallbackQuery, principal: Principal | None = None) -> None:
    builder = (
        message("Moderation", "gavel")
        .bullet("/warn – warn a member", "warn")
        .bullet("/mute [minutes] – temporarily restrict", "lock")
        .bullet("/ban – remove permanently", "ban")
        .bullet("/purge [n] – delete recent messages", "trash")
        .bullet("/antispam – anti-spam policy", "shield")
    )
    if call.message:
        await call.message.edit_text(
            builder.build(),
            reply_markup=keyboard([[Btn("Back", callback_data="core:help", emoji="arrow")]]),
        )


@router.callback_query(F.data == "core:close")
async def cb_close(call: CallbackQuery) -> None:
    if call.message:
        try:
            await call.message.delete()
        except Exception:
            await call.message.edit_text(message("Closed", "check").build())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    await message.answer(message("Cancelled", "cross").build())


@router.message(Command(commands=["ping"]))
async def cmd_ping(message: Message) -> None:
    await message.answer(message().line(p("spark") + " <b>Pong</b>").build())
