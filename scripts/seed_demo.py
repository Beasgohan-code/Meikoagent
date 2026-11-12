"""Seed a *realistic but clearly marked* demo dataset for local evaluation.

Every row created here is real data in the real schema: groups observed by a
bot, moderation actions, warnings, reports, plugin installations, a workflow,
a scheduled task and activity events. Nothing is mocked in the API layer, and
the bot account is flagged ``demo`` in its settings so it is easy to remove:

    python scripts/seed_demo.py --remove

The script never contacts Telegram and never creates a fake "verified" token.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEMO_SETTING_KEY = "demo_seed"


async def seed(telegram_id: int) -> dict[str, int]:
    from sqlalchemy import select

    from meiko.cache import init_cache
    from meiko.config import get_settings
    from meiko.db.base import create_all, init_engine, session_scope
    from meiko.db.models import (
        ActivityEvent,
        BotAccount,
        BotGroup,
        GroupMember,
        GroupSettings,
        ModerationAction,
        PluginInstallation,
        Plugin,
        PluginLog,
        Report,
        ScheduledTask,
        User,
        Warning,
        Workflow,
    )
    from meiko.services.core import BotService, PluginService, UserService
    from meiko.services.seed import seed_platform

    settings = get_settings()
    init_engine(settings)
    await create_all()
    await init_cache()

    counts: dict[str, int] = {}
    async with session_scope() as session:
        await seed_platform(session)
        user = await UserService(session).get_or_create_telegram_user(
            telegram_id, username="demo_operator", first_name="Demo Operator"
        )
        existing = await session.scalar(
            select(BotAccount).where(
                BotAccount.owner_id == user.id,
                BotAccount.name == "MEIKO Demo Guardian",
            )
        )
        if existing is not None:
            return {"bots": 0, "note": "demo bot already present"}

        service = BotService(session)
        bot = await service.register(
            user.id,
            token="700000001:" + "demo" + "D" * 31,
            name="MEIKO Demo Guardian",
        )
        bot.settings = {**(bot.settings or {}), DEMO_SETTING_KEY: True}
        bot.description = "Demo bot created by scripts/seed_demo.py"
        bot.token_valid = False
        bot.last_error = "demo account: token is not a real Telegram token"
        counts["bots"] = 1

        now = datetime.utcnow()
        group_specs = [
            ("Meiko Support Hub", "meikosupporthub", 1840, "administrator"),
            ("Kerala Devs", "keraladevs", 962, "administrator"),
            ("Product Feedback", None, 431, "member"),
        ]
        groups: list[BotGroup] = []
        for index, (title, username, members, status) in enumerate(group_specs):
            group = BotGroup(
                bot_id=bot.id,
                chat_id=-1001234567800 - index,
                title=title,
                username=username,
                chat_type="supergroup",
                member_count=members,
                bot_status=status,
                is_active=True,
                last_activity_at=now - timedelta(minutes=12 * (index + 1)),
            )
            session.add(group)
            await session.flush()
            session.add(
                GroupSettings(
                    group_id=group.id,
                    welcome_enabled=True,
                    welcome_text="Welcome {user} to {chat}! Read the rules with /rules.",
                    goodbye_enabled=index == 0,
                    goodbye_text="{user} left the chat.",
                    verification_enabled=index == 0,
                    auto_delete_seconds=0,
                    slow_mode_seconds=30 if index == 0 else 0,
                    antispam={
                        "flood_enabled": True,
                        "duplicate_enabled": True,
                        "links_enabled": True,
                        "invites_enabled": True,
                        "mentions_enabled": True,
                        "keywords_enabled": index == 0,
                        "blocked_keywords": ["free crypto", "airdrop"] if index == 0 else [],
                        "action": "mute" if index == 0 else "warn",
                        "mute_seconds": 600,
                        "warn_threshold": 3,
                    },
                    moderation={"default_action": "warn"},
                    logging_enabled=True,
                )
            )
            groups.append(group)
        counts["groups"] = len(groups)

        member_names = [
            ("Aisha", "aisha_k", False, True),
            ("Rahul", "rahul_dev", True, False),
            ("Meera", "meera", False, False),
            ("Jin", "jin_ops", False, False),
            ("Nadia", "nadia", False, True),
        ]
        members_created = 0
        for group in groups:
            for offset, (first, username, is_admin, is_trusted) in enumerate(member_names):
                session.add(
                    GroupMember(
                        group_id=group.id,
                        telegram_id=800000000 + offset + group.id * 10,
                        username=username,
                        first_name=first,
                        is_admin=is_admin,
                        is_trusted=is_trusted,
                        is_muted=False,
                        warnings_count=1 if offset == 3 else 0,
                        messages_count=40 + offset * 7,
                        joined_at=now - timedelta(days=20 - offset),
                        last_seen_at=now - timedelta(hours=offset + 1),
                    )
                )
                members_created += 1
        counts["members"] = members_created

        action_specs = [
            ("warn", "Spamming links", "automation", 120),
            ("mute", "Flood: 9 messages in 8s", "automation", 95),
            ("delete", "Blocked keyword: free crypto", "automation", 70),
            ("ban", "Scam invite link", "manual", 44),
            ("warn", "Off-topic in support thread", "manual", 22),
            ("kick", "Repeated warnings", "automation", 8),
        ]
        for action, reason, source, minutes_ago in action_specs:
            session.add(
                ModerationAction(
                    bot_id=bot.id,
                    group_id=groups[0].id,
                    chat_id=groups[0].chat_id,
                    target_id=800000003,
                    moderator_id=None if source == "automation" else user.telegram_id,
                    action=action,
                    reason=reason,
                    duration_seconds=600 if action == "mute" else 0,
                    source=source,
                    created_at=now - timedelta(minutes=minutes_ago),
                )
            )
            session.add(
                ActivityEvent(
                    bot_id=bot.id,
                    group_id=groups[0].id,
                    kind="moderation",
                    title=f"{action}: {reason}",
                    severity="warning" if action in ("warn", "mute") else "error",
                    created_at=now - timedelta(minutes=minutes_ago),
                )
            )
        counts["moderation"] = len(action_specs)

        session.add(
            Warning(
                bot_id=bot.id,
                group_id=groups[0].id,
                telegram_id=800000003,
                moderator_id=user.telegram_id,
                reason="Off-topic in support thread",
                created_at=now - timedelta(hours=3),
            )
        )
        session.add(
            Report(
                bot_id=bot.id,
                group_id=groups[0].id,
                reporter_id=800000001,
                target_id=800000004,
                reason="Advertising another chat",
                status="open",
                created_at=now - timedelta(hours=5),
            )
        )

        for day in range(6):
            session.add(
                ActivityEvent(
                    bot_id=bot.id,
                    group_id=groups[day % len(groups)].id,
                    kind="message",
                    title=f"{12 + day * 4} messages processed",
                    severity="info",
                    created_at=now - timedelta(days=day, hours=2),
                )
            )

        session.add(
            Workflow(
                bot_id=bot.id,
                name="Welcome boosters",
                description="Thank members who join with the invite link.",
                enabled=True,
                trigger={"event": "join"},
                conditions=[{"fact": "user.is_new_member", "op": "is_true"}],
                actions=[{"type": "send_message", "text": "Welcome aboard!"}],
                version=1,
            )
        )
        session.add(
            ScheduledTask(
                bot_id=bot.id,
                group_id=groups[0].id,
                name="Daily rules digest",
                kind="announcement",
                cron="0 9 * * *",
                next_run_at=now + timedelta(hours=6),
                payload={"text": "Reminder: keep it civil."},
                status="pending",
            )
        )

        plugin_service = PluginService(session)
        for slug in ("anti_spam", "welcome", "reports", "analytics"):
            try:
                installation = await plugin_service.install(slug, bot_id=bot.id, user_id=user.id)
                if slug == "anti_spam":
                    # Only keys declared in the manifest schema are accepted.
                    await plugin_service.configure(
                        installation,
                        {"action": "mute", "flood_messages": 7, "block_links": True},
                    )
                await plugin_service.enable(installation)
                session.add(
                    PluginLog(
                        installation_id=installation.id,
                        level="info",
                        message=f"{slug} enabled for demo bot",
                        created_at=now - timedelta(minutes=30),
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive
                print(f"  ! could not enable {slug}: {exc}")
                session.add(
                    ActivityEvent(
                        bot_id=bot.id,
                        kind="plugin.error",
                        title=f"could not seed {slug}: {exc}",
                        severity="error",
                    )
                )
        counts["plugins"] = 4

    return counts


async def remove() -> dict[str, int]:
    from sqlalchemy import delete, select

    from meiko.cache import init_cache
    from meiko.db.base import create_all, init_engine, session_scope
    from meiko.config import get_settings
    from meiko.db.models import (
        ActivityEvent,
        BotAccount,
        BotGroup,
        GroupMember,
        ModerationAction,
        Report,
        ScheduledTask,
        Warning,
        Workflow,
    )

    settings = get_settings()
    init_engine(settings)
    await create_all()
    await init_cache()

    removed: dict[str, int] = {}
    async with session_scope() as session:
        bots = (
            await session.scalars(
                select(BotAccount).where(BotAccount.settings[DEMO_SETTING_KEY].as_boolean().is_(True))
            )
        ).all()
        if not bots:
            return {"bots": 0}
        bot_ids = [bot.id for bot in bots]
        group_ids = [
            row for row in await session.scalars(select(BotGroup.id).where(BotGroup.bot_id.in_(bot_ids)))
        ]
        for model, column in (
            (ActivityEvent, ActivityEvent.bot_id),
            (ModerationAction, ModerationAction.bot_id),
            (Warning, Warning.bot_id),
            (Report, Report.bot_id),
            (Workflow, Workflow.bot_id),
            (ScheduledTask, ScheduledTask.bot_id),
            (GroupMember, GroupMember.group_id),
        ):
            if model is GroupMember:
                if not group_ids:
                    continue
                result = await session.execute(delete(model).where(column.in_(group_ids)))
            else:
                result = await session.execute(delete(model).where(column.in_(bot_ids)))
            removed[model.__tablename__] = result.rowcount or 0
        from meiko.db.models import GroupSettings, PluginInstallation

        await session.execute(delete(GroupSettings).where(GroupSettings.group_id.in_(group_ids or [0])))
        await session.execute(delete(PluginInstallation).where(PluginInstallation.bot_id.in_(bot_ids)))
        await session.execute(delete(BotGroup).where(BotGroup.bot_id.in_(bot_ids)))
        await session.execute(delete(BotAccount).where(BotAccount.id.in_(bot_ids)))
        removed["bots"] = len(bot_ids)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed or remove MEIKO AGENT demo data")
    parser.add_argument("--telegram-id", type=int, default=700000001, help="owner telegram id for the demo account")
    parser.add_argument("--remove", action="store_true", help="delete demo rows instead of creating them")
    args = parser.parse_args()

    if args.remove:
        result = asyncio.run(remove())
        print("removed:", result)
    else:
        result = asyncio.run(seed(args.telegram_id))
        print("seeded:", result)
        print(f"sign in with the development login using telegram id {args.telegram_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
