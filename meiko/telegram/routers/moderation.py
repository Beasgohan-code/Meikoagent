"""Moderation router: warn/mute/ban/kick/purge/notes/antispam/settings.

Every destructive command checks the caller's :class:`Principal` against the
capability it needs, targets must be resolvable (reply or id) and each action is
routed through :class:`ModerationExecutor` so the audit trail is always written.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatPermissions, Message

from meiko.db.base import session_scope
from meiko.db.models import BotGroup, GroupMember, Warning
from meiko.moderation.antispam import AntiSpamPolicy
from meiko.moderation.events import ModerationEvent
from meiko.moderation.executor import ModerationExecutor, MUTE_PERMISSIONS
from meiko.security.permissions import Principal, Role
from meiko.services.core import GroupService, ModerationService
from meiko.ui import Btn, duration, keyboard, message, user_mention
from meiko.ui.engine import MessageBuilder

from sqlalchemy import func, select

router = Router(name="moderation")

DENIED = "You need moderator rights to do that."


def parse_target(message: Message, command: CommandObject) -> tuple[int, str] | None:
    """Resolve a target from a reply or from the command arguments."""
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        return user.id, user_mention(user.id, user.first_name or "member")
    args = (command.args or "").split()
    if args:
        candidate = args[0].lstrip("@")
        if candidate.lstrip("-").isdigit():
            return int(candidate), f"<code>{candidate}</code>"
        return None
    return None


def parse_duration(args: str | None, default: int = 0) -> int:
    """Parse ``10m``, ``2h``, ``1d`` or a raw number of minutes."""
    if not args:
        return default
    token = args.split()[0].lower()
    multiplier = 60
    if token.endswith("s"):
        multiplier, token = 1, token[:-1]
    elif token.endswith("m"):
        multiplier = 60
        token = token[:-1]
    elif token.endswith("h"):
        multiplier = 3600
        token = token[:-1]
    elif token.endswith("d"):
        multiplier = 86400
        token = token[:-1]
    try:
        return int(token) * multiplier
    except ValueError:
        return default


async def _group_id(bot_id: int, chat_id: int) -> int | None:
    async with session_scope() as session:
        group = await session.scalar(
            select(BotGroup).where(BotGroup.bot_id == bot_id, BotGroup.chat_id == chat_id)
        )
        return group.id if group else None


def _event(bot_id: int, message: Message, group_id: int | None) -> ModerationEvent:
    return ModerationEvent(
        kind="command",
        bot_id=bot_id,
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else None,
        group_id=group_id,
        message_id=message.message_id,
        message_thread_id=message.message_thread_id,
    )


async def _audit(bot_id: int):
    session = session_scope()
    return session


@router.message(Command("warn"))
async def cmd_warn(
    message: Message,
    command: CommandObject,
    principal: Principal | None = None,
    meiko=None,
) -> None:
    if principal is None or not principal.can("moderation.warn"):
        await message.reply(DENIED)
        return
    target = parse_target(message, command)
    if target is None:
        await message.reply("Reply to a member or pass their id: <code>/warn 123 [reason]</code>")
        return
    target_id, mention = target
    reason = (command.args or "").partition(" ")[2].strip() or "no reason given"
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        session.add(
            Warning(
                bot_id=meiko.bot_id,
                group_id=group_id,
                telegram_id=target_id,
                moderator_id=message.from_user.id,
                reason=reason[:500],
            )
        )
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(Warning)
                .where(
                    Warning.bot_id == meiko.bot_id,
                    Warning.group_id == group_id,
                    Warning.telegram_id == target_id,
                    Warning.active.is_(True),
                )
            )
            or 1
        )
        await ModerationService(session).record(
            meiko.bot_id,
            "warn",
            group_id=group_id,
            chat_id=message.chat.id,
            target_id=target_id,
            moderator_id=message.from_user.id,
            reason=reason,
            meta={"warnings": count},
        )
    builder = (
        message("Warning issued", "warn")
        .key_value("Member", mention, value_raw=True)
        .key_value("Warnings", count)
        .key_value("Reason", reason)
    )
    await message.reply(builder.build())


@router.message(Command("warnings", "warns"))
async def cmd_warnings(message: Message, command: CommandObject, meiko=None) -> None:
    target = parse_target(message, command)
    if target is None:
        await message.reply("Reply to a member to inspect their warnings.")
        return
    target_id, mention = target
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(Warning)
                .where(
                    Warning.bot_id == meiko.bot_id,
                    Warning.group_id == group_id,
                    Warning.telegram_id == target_id,
                    Warning.active.is_(True),
                )
                .order_by(Warning.created_at.desc())
                .limit(10)
            )
        ).all()
    builder = message("Warnings", "warn").key_value("Member", mention, value_raw=True)
    if not rows:
        builder.line("No active warnings.")
    for index, row in enumerate(rows, 1):
        builder.bullet(f"#{index} {row.reason or 'no reason'} – {row.created_at:%Y-%m-%d}", "warn")
    await message.reply(builder.build())


@router.message(Command("unwarn"))
async def cmd_unwarn(
    message: Message, command: CommandObject, principal: Principal | None = None, meiko=None
) -> None:
    if principal is None or not principal.can("moderation.warn"):
        await message.reply(DENIED)
        return
    target = parse_target(message, command)
    if target is None:
        await message.reply("Reply to a member to clear a warning.")
        return
    target_id, mention = target
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        warn = await session.scalar(
            select(Warning)
            .where(
                Warning.bot_id == meiko.bot_id,
                Warning.group_id == group_id,
                Warning.telegram_id == target_id,
                Warning.active.is_(True),
            )
            .order_by(Warning.created_at.desc())
        )
        if warn is None:
            await message.reply("That member has no active warnings.")
            return
        warn.active = False
        warn.cleared_at = datetime.now(timezone.utc).replace(tzinfo=None)
        warn.cleared_by = message.from_user.id
        await ModerationService(session).record(
            meiko.bot_id,
            "unwarn",
            group_id=group_id,
            chat_id=message.chat.id,
            target_id=target_id,
            moderator_id=message.from_user.id,
        )
    await message.reply(
        message("Warning cleared", "check").key_value("Member", mention, value_raw=True).build()
    )


@router.message(Command("mute"))
async def cmd_mute(
    message: Message, command: CommandObject, principal: Principal | None = None, meiko=None
) -> None:
    if principal is None or not principal.can("moderation.mute"):
        await message.reply(DENIED)
        return
    target = parse_target(message, command)
    if target is None:
        await message.reply("Reply to a member: <code>/mute 10m reason</code>")
        return
    target_id, mention = target
    seconds = parse_duration(command.args, default=600)
    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    await message.bot.restrict_chat_member(
        chat_id=message.chat.id,
        user_id=target_id,
        permissions=MUTE_PERMISSIONS,
        until_date=until,
    )
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        await ModerationService(session).record(
            meiko.bot_id,
            "mute",
            group_id=group_id,
            chat_id=message.chat.id,
            target_id=target_id,
            moderator_id=message.from_user.id,
            duration_seconds=seconds,
        )
    await message.reply(
        message("Member muted", "lock")
        .key_value("Member", mention, value_raw=True)
        .key_value("Duration", duration(seconds), "clock")
        .build()
    )


@router.message(Command("unmute"))
async def cmd_unmute(
    message: Message, command: CommandObject, principal: Principal | None = None, meiko=None
) -> None:
    if principal is None or not principal.can("moderation.mute"):
        await message.reply(DENIED)
        return
    target = parse_target(message, command)
    if target is None:
        await message.reply("Reply to a member to unmute them.")
        return
    target_id, mention = target
    await message.bot.restrict_chat_member(
        chat_id=message.chat.id,
        user_id=target_id,
        permissions=ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        ),
    )
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        await ModerationService(session).record(
            meiko.bot_id, "unmute", group_id=group_id, chat_id=message.chat.id,
            target_id=target_id, moderator_id=message.from_user.id,
        )
    await message.reply(
        message("Member unmuted", "unmute").key_value("Member", mention, value_raw=True).build()
    )


@router.message(Command("kick"))
async def cmd_kick(
    message: Message, command: CommandObject, principal: Principal | None = None, meiko=None
) -> None:
    if principal is None or not principal.can("moderation.kick"):
        await message.reply(DENIED)
        return
    target = parse_target(message, command)
    if target is None:
        await message.reply("Reply to a member to kick them.")
        return
    target_id, mention = target
    await message.bot.ban_chat_member(message.chat.id, target_id)
    await message.bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        await ModerationService(session).record(
            meiko.bot_id, "kick", group_id=group_id, chat_id=message.chat.id,
            target_id=target_id, moderator_id=message.from_user.id,
        )
    await message.reply(
        message("Member kicked", "arrow").key_value("Member", mention, value_raw=True).build()
    )


@router.message(Command("ban"))
async def cmd_ban(
    message: Message, command: CommandObject, principal: Principal | None = None, meiko=None
) -> None:
    if principal is None or not principal.can("moderation.ban"):
        await message.reply(DENIED)
        return
    target = parse_target(message, command)
    if target is None:
        await message.reply("Reply to a member or pass an id to ban them.")
        return
    target_id, mention = target
    reason = (command.args or "").partition(" ")[2].strip()
    await message.bot.ban_chat_member(message.chat.id, target_id)
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        await ModerationService(session).record(
            meiko.bot_id, "ban", group_id=group_id, chat_id=message.chat.id,
            target_id=target_id, moderator_id=message.from_user.id, reason=reason,
        )
    await message.reply(
        message("Member banned", "ban")
        .key_value("Member", mention, value_raw=True)
        .key_value("Reason", reason or "none")
        .build()
    )


@router.message(Command("unban"))
async def cmd_unban(
    message: Message, command: CommandObject, principal: Principal | None = None, meiko=None
) -> None:
    if principal is None or not principal.can("moderation.ban"):
        await message.reply(DENIED)
        return
    target = parse_target(message, command)
    if target is None:
        await message.reply("Pass the user id to unban: <code>/unban 123456</code>")
        return
    target_id, mention = target
    await message.bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        await ModerationService(session).record(
            meiko.bot_id, "unban", group_id=group_id, chat_id=message.chat.id,
            target_id=target_id, moderator_id=message.from_user.id,
        )
    await message.reply(
        message("Member unbanned", "check").key_value("Member", mention, value_raw=True).build()
    )


@router.message(Command("purge"))
async def cmd_purge(
    message: Message, command: CommandObject, principal: Principal | None = None, meiko=None
) -> None:
    if principal is None or not principal.can("moderation.purge"):
        await message.reply(DENIED)
        return
    count = 10
    if command.args and command.args.split()[0].isdigit():
        count = min(int(command.args.split()[0]), 100)
    deleted = 0
    for offset in range(1, count + 1):
        try:
            await message.bot.delete_message(message.chat.id, message.message_id - offset)
            deleted += 1
        except Exception:
            continue
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        await ModerationService(session).record(
            meiko.bot_id, "purge", group_id=group_id, chat_id=message.chat.id,
            moderator_id=message.from_user.id, meta={"deleted": deleted},
        )
    confirmation = await message.answer(
        message("Purged", "trash").key_value("Messages removed", deleted).build()
    )
    try:
        import asyncio

        await asyncio.sleep(4)
        await confirmation.bot.delete_message(confirmation.chat.id, confirmation.message_id)
    except Exception:
        pass


@router.message(Command("note"))
async def cmd_note(
    message: Message, command: CommandObject, principal: Principal | None = None, meiko=None
) -> None:
    if principal is None or not principal.can("moderation.settings"):
        await message.reply(DENIED)
        return
    target = parse_target(message, command)
    if target is None:
        await message.reply("Reply to a member to attach a note.")
        return
    target_id, mention = target
    note = (command.args or "").partition(" ")[2].strip()
    if not note:
        await message.reply("Provide the note text after the id.")
        return
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        member = await session.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.telegram_id == target_id
            )
        )
        if member is None:
            member = GroupMember(group_id=group_id, telegram_id=target_id)
            session.add(member)
        member.note = note[:2000]
    await message.reply(
        message("Note saved", "pin").key_value("Member", mention, value_raw=True).build()
    )


@router.message(Command("trust"))
async def cmd_trust(
    message: Message, command: CommandObject, principal: Principal | None = None, meiko=None
) -> None:
    if principal is None or not principal.can("moderation.settings"):
        await message.reply(DENIED)
        return
    target = parse_target(message, command)
    if target is None:
        await message.reply("Reply to a member to trust them.")
        return
    target_id, mention = target
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        member = await session.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.telegram_id == target_id
            )
        )
        if member is None:
            member = GroupMember(group_id=group_id, telegram_id=target_id)
            session.add(member)
        member.is_trusted = not member.is_trusted
        trusted = member.is_trusted
    await message.reply(
        message(
            "Member trusted" if trusted else "Trust removed", "check" if trusted else "cross"
        )
        .key_value("Member", mention, value_raw=True)
        .build()
    )


@router.message(Command("antispam"))
async def cmd_antispam(
    message: Message, command: CommandObject, principal: Principal | None = None, meiko=None
) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("Anti-spam settings are per group.")
        return
    if principal is None or not principal.can("moderation.settings"):
        await message.reply("You need administrator rights to change anti-spam settings.")
        return
    args = (command.args or "").split()
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        settings = await GroupService(session).settings(group_id)
        policy = AntiSpamPolicy.from_dict(settings.antispam or {})
        if args:
            key = args[0].lower()
            value = args[1] if len(args) > 1 else None
            if key == "off":
                policy.flood_enabled = policy.duplicate_enabled = False
                policy.links_enabled = policy.invites_enabled = False
            elif key == "on":
                policy.flood_enabled = policy.duplicate_enabled = True
                policy.links_enabled = policy.invites_enabled = True
            elif key == "limit" and value and value.isdigit():
                policy.flood_messages = int(value)
            elif key == "action" and value in ("warn", "mute", "kick", "ban", "delete"):
                policy.action = value
            elif key == "links" and value in ("on", "off"):
                policy.links_enabled = value == "on"
            elif key.startswith("keyword") and value:
                policy.keywords_enabled = True
                policy.blocked_keywords = (policy.blocked_keywords or []) + [value]
            else:
                await message.reply("Usage: /antispam on|off|limit 7|action mute|links off|keyword word")
                return
            settings.antispam = policy.to_dict()
    builder = (
        message("Anti-spam settings", "shield")
        .key_value("Flood", f"{policy.flood_messages} / {policy.flood_window_seconds}s")
        .key_value("Duplicates", "on" if policy.duplicate_enabled else "off")
        .key_value("Links", "blocked" if policy.links_enabled and not policy.allow_links else "allowed")
        .key_value("Invites", "blocked" if policy.invites_enabled else "allowed")
        .key_value("Action", policy.action)
    )
    await message.reply(builder.build())


@router.message(Command("modlog"))
async def cmd_modlog(
    message: Message, principal: Principal | None = None, meiko=None
) -> None:
    if principal is None or not principal.can("moderation.warn"):
        await message.reply(DENIED)
        return
    group_id = await _group_id(meiko.bot_id, message.chat.id)
    async with session_scope() as session:
        history = await ModerationService(session).history(
            meiko.bot_id, group_id=group_id, limit=10
        )
    builder = message("Moderation log", "gavel")
    if not history:
        builder.line("No moderation actions recorded yet.")
    for record in history:
        builder.bullet(
            f"{record.action.upper()} • {record.target_id or '—'} • "
            f"{record.reason or 'no reason'} • {record.created_at:%d %b %H:%M}",
            "gavel",
        )
    await message.reply(builder.build())
