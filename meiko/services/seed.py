"""Idempotent platform seeding: plugin catalogue, bot templates, owner record."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meiko.config import get_settings
from meiko.db.models import BotTemplate, Plugin, SystemSetting, User
from meiko.logging_setup import get_logger
from meiko.plugins.builtin import BUILTIN_MANIFESTS

log = get_logger("services.seed")

TEMPLATES = [
    {
        "slug": "community-guardian",
        "name": "Community Guardian",
        "description": "Moderation-first community bot: anti-spam, verification, welcome cards and reports.",
        "category": "moderation",
        "icon": "shield",
        "plugins": ["anti_spam", "welcome", "security", "reports", "utilities"],
        "config": {"moderation": {"default_action": "mute"}, "welcome": True},
    },
    {
        "slug": "support-desk",
        "name": "Support Desk",
        "description": "Answer questions with filters, reports and AI assistance.",
        "category": "support",
        "icon": "message",
        "plugins": ["filters", "reports", "ai", "utilities"],
        "config": {"filters": True},
    },
    {
        "slug": "analytics-hub",
        "name": "Analytics Hub",
        "description": "Track activity, leaderboards and scheduled digests.",
        "category": "analytics",
        "icon": "stats",
        "plugins": ["analytics", "utilities"],
        "config": {"digest": True},
    },
    {
        "slug": "automation-lab",
        "name": "Automation Lab",
        "description": "Workflow automation with integrations and webhooks.",
        "category": "automation",
        "icon": "gear",
        "plugins": ["automation", "integrations", "filters"],
        "config": {},
    },
]


async def seed_plugins(session: AsyncSession) -> int:
    created = 0
    for manifest in BUILTIN_MANIFESTS:
        plugin = await session.scalar(select(Plugin).where(Plugin.slug == manifest.slug))
        payload = {
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "author": manifest.author,
            "category": manifest.category,
            "icon": manifest.icon,
            "license": manifest.license,
            "homepage": manifest.homepage,
            "permissions": list(manifest.permissions),
            "dependencies": list(manifest.dependencies),
            "config_schema": manifest.config_schema,
            "manifest": manifest.to_dict(),
            "status": "approved",
            "is_official": True,
            "source": "builtin",
        }
        if plugin is None:
            session.add(Plugin(slug=manifest.slug, **payload))
            created += 1
        else:
            for key, value in payload.items():
                setattr(plugin, key, value)
    await session.flush()
    return created


async def seed_templates(session: AsyncSession) -> int:
    created = 0
    for template in TEMPLATES:
        record = await session.scalar(
            select(BotTemplate).where(BotTemplate.slug == template["slug"])
        )
        if record is None:
            session.add(BotTemplate(**template, is_public=True))
            created += 1
        else:
            for key, value in template.items():
                setattr(record, key, value)
    await session.flush()
    return created


async def seed_owner(session: AsyncSession, telegram_id: int) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=None,
            first_name="Platform Owner",
            role="owner",
            is_owner=True,
        )
        session.add(user)
        await session.flush()
    else:
        user.is_owner = True
        user.role = "owner"
    return user


async def seed_platform(session: AsyncSession) -> dict[str, int]:
    plugins = await seed_plugins(session)
    templates = await seed_templates(session)
    owners = 0
    for telegram_id in get_settings().owner_ids:
        await seed_owner(session, telegram_id)
        owners += 1
    setting = await session.get(SystemSetting, "platform")
    if setting is None:
        session.add(SystemSetting(key="platform", value={"bot_api": "10.4", "seeded": True}))
    await session.flush()
    if plugins or templates:
        log.info("seeded %s plugins, %s templates, %s owners", plugins, templates, owners)
    return {"plugins": plugins, "templates": templates, "owners": owners}
