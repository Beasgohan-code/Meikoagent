"""Shared service contracts used by bot, API, plugins and workers.

Business logic lives here – never in handlers or route functions – so the bot
and the webapp cannot drift apart.  Every service is tenant aware: callers pass
an owning ``user_id``/``bot_id`` and the service enforces isolation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meiko.db.models import (
    ActivityEvent,
    AuditLog,
    BotAccount,
    BotCommandRecord,
    BotGroup,
    BotTemplate,
    CommandExecution,
    GroupSettings,
    ModerationAction,
    Plugin,
    PluginInstallation,
    PluginLog,
    RuntimeEvent,
    User,
    utcnow,
)
from meiko.logging_setup import get_logger
from meiko.security.crypto import (
    DecryptionError,
    decrypt_secret,
    encrypt_secret,
    token_fingerprint,
)

log = get_logger("services")


class NotFound(LookupError):
    """Requested resource does not exist *or* is not visible to the actor."""


class Conflict(ValueError):
    """State conflict (duplicate token, invalid transition …)."""


class ValidationError(ValueError):
    """Input rejected by a service level rule."""


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_telegram_user(
        self,
        telegram_id: int,
        *,
        username: str | None = None,
        first_name: str = "",
        last_name: str = "",
        language_code: str = "en",
        photo_url: str | None = None,
    ) -> User:
        user = await self.session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name[:128],
                last_name=last_name[:128],
                language_code=language_code,
                photo_url=photo_url,
                role="user",
            )
            self.session.add(user)
            await self.session.flush()
        else:
            user.username = username or user.username
            user.first_name = first_name[:128] or user.first_name
            user.last_name = last_name[:128] or user.last_name
            if photo_url:
                user.photo_url = photo_url
        user.last_seen_at = utcnow()
        await self.session.flush()
        return user

    async def get(self, user_id: int) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise NotFound("user not found")
        return user

    async def set_role(self, user_id: int, role: str, *, actor_is_owner: bool) -> User:
        if not actor_is_owner:
            raise PermissionError("only the platform owner may change roles")
        user = await self.get(user_id)
        if role not in ("user", "admin", "owner"):
            raise ValidationError("invalid role")
        user.role = role
        user.is_owner = role == "owner"
        await self.session.flush()
        return user


# --------------------------------------------------------------------------- #
# Bots
# --------------------------------------------------------------------------- #
BOT_STATUSES = {"draft", "stopped", "starting", "running", "error"}


class BotService:
    """Every query is scoped by owner, which is what enforces tenant isolation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- queries ---------------------------------------------------------
    def _owned(self, owner_id: int, include_clones: bool = True) -> Select[Any]:
        stmt = select(BotAccount).where(BotAccount.owner_id == owner_id)
        if not include_clones:
            stmt = stmt.where(BotAccount.is_clone.is_(False))
        return stmt.order_by(BotAccount.id.desc())

    async def list_for_owner(self, owner_id: int) -> Sequence[BotAccount]:
        return list((await self.session.scalars(self._owned(owner_id))).all())

    async def get(self, bot_id: int, owner_id: int | None = None) -> BotAccount:
        bot = await self.session.get(BotAccount, bot_id)
        if bot is None or (owner_id is not None and bot.owner_id != owner_id):
            raise NotFound("bot not found")
        return bot

    async def get_by_token(self, token: str) -> BotAccount | None:
        return await self.session.scalar(
            select(BotAccount).where(BotAccount.token_fingerprint == token_fingerprint(token))
        )

    async def get_by_telegram_id(self, telegram_bot_id: int) -> BotAccount | None:
        return await self.session.scalar(
            select(BotAccount).where(BotAccount.telegram_bot_id == telegram_bot_id)
        )

    # -- mutations -------------------------------------------------------
    async def register(
        self,
        owner_id: int,
        *,
        token: str,
        name: str = "",
        username: str | None = None,
        telegram_bot_id: int | None = None,
        template_id: int | None = None,
        is_clone: bool = False,
        source_bot_id: int | None = None,
        settings: dict[str, Any] | None = None,
    ) -> BotAccount:
        self._validate_token(token)
        existing = await self.get_by_token(token)
        if existing is not None:
            raise Conflict("this bot token is already connected to the platform")
        if telegram_bot_id is not None and await self.get_by_telegram_id(telegram_bot_id):
            raise Conflict("this Telegram bot is already connected")
        bot = BotAccount(
            owner_id=owner_id,
            token_encrypted=encrypt_secret(token),
            token_fingerprint=token_fingerprint(token),
            name=(name or username or "Meiko Bot")[:128],
            username=username,
            telegram_bot_id=telegram_bot_id,
            status="draft",
            is_clone=is_clone,
            source_bot_id=source_bot_id,
            template_id=template_id,
            settings=settings or {},
            limits={"max_groups": 100, "max_plugins": 25},
        )
        self.session.add(bot)
        await self.session.flush()
        await ActivityEventService(self.session).record(
            bot_id=bot.id, kind="bot.connected", title="Bot connected", severity="info"
        )
        return bot

    async def verify_identity(
        self,
        bot: BotAccount,
        *,
        telegram_bot_id: int,
        username: str | None,
        first_name: str | None = None,
    ) -> BotAccount:
        other = await self.get_by_telegram_id(telegram_bot_id)
        if other is not None and other.id != bot.id:
            raise Conflict("this Telegram bot is already connected by another token")
        bot.telegram_bot_id = telegram_bot_id
        bot.username = username or bot.username
        bot.name = (first_name or bot.name)[:128]
        bot.token_valid = True
        bot.status = "stopped"
        await self.session.flush()
        return bot

    async def replace_token(self, bot: BotAccount, token: str) -> BotAccount:
        self._validate_token(token)
        existing = await self.get_by_token(token)
        if existing is not None and existing.id != bot.id:
            raise Conflict("token already in use")
        bot.token_encrypted = encrypt_secret(token)
        bot.token_fingerprint = token_fingerprint(token)
        bot.token_valid = False
        await self.session.flush()
        return bot

    def token(self, bot: BotAccount) -> str:
        try:
            return decrypt_secret(bot.token_encrypted)
        except DecryptionError as exc:  # pragma: no cover
            raise ValidationError("stored bot token cannot be decrypted with the active key") from exc

    async def set_status(self, bot: BotAccount, status: str, error: str | None = None) -> BotAccount:
        if status not in BOT_STATUSES:
            raise ValidationError(f"invalid bot status {status!r}")
        bot.status = status
        if status == "running":
            bot.last_started_at = utcnow()
            bot.last_error = None
        elif status in ("stopped", "error"):
            bot.last_stopped_at = utcnow()
            bot.last_error = error
        await self.session.flush()
        return bot

    async def delete(self, bot: BotAccount) -> None:
        """Delete a bot and every row that depends on it.

        SQLite does not enforce ``ON DELETE CASCADE`` unless the pragma is
        enabled, and PostgreSQL relies on the database to do it.  Deleting in
        dependency order keeps the behaviour identical on both engines and never
        leaves orphaned moderation/plugin/audit rows behind.  Clones are *not*
        deleted: they are independent bots that merely reference the source.
        """
        from sqlalchemy import delete as sa_delete, or_, select, update

        from meiko.db.models import (
            ActivityEvent,
            Appeal,
            BotAccount,
            BotCommandRecord,
            BotGroup,
            GroupFilter,
            GroupMember,
            GroupRule,
            GroupSettings,
            ModerationAction,
            PluginInstallation,
            PluginLog,
            Report,
            RuntimeEvent,
            ScheduledTask,
            UsageRecord,
            Warning,
            Webhook,
            WebhookDelivery,
        )

        bot_id = bot.id
        group_ids = list(
            (await self.session.scalars(select(BotGroup.id).where(BotGroup.bot_id == bot_id))).all()
        )
        installation_ids = list(
            (
                await self.session.scalars(
                    select(PluginInstallation.id).where(PluginInstallation.bot_id == bot_id)
                )
            ).all()
        )
        webhook_ids = list(
            (await self.session.scalars(select(Webhook.id).where(Webhook.bot_id == bot_id))).all()
        )
        report_ids = list(
            (await self.session.scalars(select(Report.id).where(Report.bot_id == bot_id))).all()
        )

        if installation_ids:
            await self.session.execute(
                sa_delete(PluginLog).where(PluginLog.installation_id.in_(installation_ids))
            )
        if webhook_ids:
            await self.session.execute(
                sa_delete(WebhookDelivery).where(WebhookDelivery.webhook_id.in_(webhook_ids))
            )
        if report_ids:
            await self.session.execute(sa_delete(Appeal).where(Appeal.report_id.in_(report_ids)))

        for model in (
            Appeal,
            Report,
            Warning,
            GroupRule,
            GroupMember,
            GroupFilter,
        ):
            if group_ids:
                await self.session.execute(sa_delete(model).where(model.group_id.in_(group_ids)))
        if group_ids:
            await self.session.execute(
                sa_delete(GroupSettings).where(GroupSettings.group_id.in_(group_ids))
            )
            await self.session.execute(
                sa_delete(ActivityEvent).where(ActivityEvent.group_id.in_(group_ids))
            )
            await self.session.execute(sa_delete(BotGroup).where(BotGroup.bot_id == bot_id))

        for model in (
            ModerationAction,
            BotCommandRecord,
            ScheduledTask,
            PluginInstallation,
            Webhook,
            ActivityEvent,
            UsageRecord,
        ):
            await self.session.execute(sa_delete(model).where(model.bot_id == bot_id))

        # Runtime events are an audit trail: keep them, but detach the bot so the
        # foreign key does not block deletion.
        await self.session.execute(
            update(RuntimeEvent).where(RuntimeEvent.bot_id == bot_id).values(bot_id=None)
        )
        await self.session.execute(
            update(BotAccount)
            .where(BotAccount.source_bot_id == bot_id)
            .values(source_bot_id=None)
        )
        await self.session.delete(bot)
        await self.session.flush()

    async def clone_from(
        self,
        source: BotAccount,
        owner_id: int,
        *,
        token: str,
        name: str | None = None,
        username: str | None = None,
        telegram_bot_id: int | None = None,
        copy_plugins: bool = True,
    ) -> BotAccount:
        """Create an independent bot that inherits configuration, not runtime."""
        clone = await self.register(
            owner_id,
            token=token,
            name=name or f"{source.name} (clone)",
            username=username,
            telegram_bot_id=telegram_bot_id,
            is_clone=True,
            source_bot_id=source.id,
            settings=dict(source.settings or {}),
        )
        clone.description = source.description
        clone.runtime_mode = source.runtime_mode
        if copy_plugins:
            installs = await self.session.scalars(
                select(PluginInstallation).where(
                    PluginInstallation.bot_id == source.id, PluginInstallation.scope == "bot"
                )
            )
            for install in installs:
                self.session.add(
                    PluginInstallation(
                        plugin_id=install.plugin_id,
                        bot_id=clone.id,
                        scope="bot",
                        state="installed",
                        version=install.version,
                        config=dict(install.config or {}),
                        installed_by=owner_id,
                    )
                )
        await self.session.flush()
        return clone

    @staticmethod
    def _validate_token(token: str) -> None:
        token = (token or "").strip()
        if ":" not in token:
            raise ValidationError("a bot token has the form 123456789:AA…")
        head, _, tail = token.partition(":")
        if not head.isdigit() or len(tail) < 20:
            raise ValidationError("malformed bot token")
        if any(ch.isspace() for ch in token):
            raise ValidationError("bot token must not contain whitespace")


# --------------------------------------------------------------------------- #
# Groups
# --------------------------------------------------------------------------- #
class GroupService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        bot_id: int,
        chat_id: int,
        *,
        title: str = "",
        username: str | None = None,
        chat_type: str = "supergroup",
        bot_status: str = "member",
        permissions: dict[str, Any] | None = None,
    ) -> BotGroup:
        group = await self.session.scalar(
            select(BotGroup).where(BotGroup.bot_id == bot_id, BotGroup.chat_id == chat_id)
        )
        if group is None:
            group = BotGroup(bot_id=bot_id, chat_id=chat_id, title=title, chat_type=chat_type)
            self.session.add(group)
            await self.session.flush()
            self.session.add(GroupSettings(group_id=group.id, welcome_text=""))
            await self.session.flush()
        group.title = title or group.title
        group.username = username or group.username
        group.bot_status = bot_status
        if permissions is not None:
            group.permissions = permissions
        group.last_activity_at = utcnow()
        await self.session.flush()
        return group

    async def list_for_bot(self, bot_id: int) -> Sequence[BotGroup]:
        return list(
            (
                await self.session.scalars(
                    select(BotGroup)
                    .where(BotGroup.bot_id == bot_id, BotGroup.is_active.is_(True))
                    .order_by(BotGroup.title)
                )
            ).all()
        )

    async def get(self, group_id: int, bot_id: int | None = None) -> BotGroup:
        group = await self.session.get(BotGroup, group_id)
        if group is None or (bot_id is not None and group.bot_id != bot_id):
            raise NotFound("group not found")
        return group

    async def settings(self, group_id: int) -> GroupSettings:
        settings = await self.session.scalar(
            select(GroupSettings).where(GroupSettings.group_id == group_id)
        )
        if settings is None:
            settings = GroupSettings(group_id=group_id)
            self.session.add(settings)
            await self.session.flush()
        return settings

    async def update_settings(self, group_id: int, **values: Any) -> GroupSettings:
        settings = await self.settings(group_id)
        allowed = set(GroupSettings.__table__.columns.keys()) - {
            "id",
            "group_id",
            "created_at",
            "updated_at",
        }
        for key, value in values.items():
            if key in allowed:
                setattr(settings, key, value)
        await self.session.flush()
        return settings


# --------------------------------------------------------------------------- #
# Moderation
# --------------------------------------------------------------------------- #
class ModerationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        bot_id: int,
        action: str,
        *,
        group_id: int | None = None,
        chat_id: int | None = None,
        target_id: int | None = None,
        moderator_id: int | None = None,
        reason: str = "",
        duration_seconds: int = 0,
        source: str = "manual",
        meta: dict[str, Any] | None = None,
    ) -> ModerationAction:
        record = ModerationAction(
            bot_id=bot_id,
            group_id=group_id,
            chat_id=chat_id,
            target_id=target_id,
            moderator_id=moderator_id,
            action=action,
            reason=reason[:2000],
            duration_seconds=duration_seconds,
            expires_at=(utcnow() + timedelta(seconds=duration_seconds))
            if duration_seconds
            else None,
            source=source,
            meta=meta or {},
        )
        self.session.add(record)
        await self.session.flush()
        await ActivityEventService(self.session).record(
            bot_id=bot_id,
            group_id=group_id,
            kind=f"moderation.{action}",
            title=f"{action} applied",
            telegram_user_id=target_id,
            severity="warning" if action in ("ban", "kick", "mute", "restrict") else "info",
            data={"reason": reason[:200]},
        )
        return record

    async def history(
        self,
        bot_id: int,
        *,
        group_id: int | None = None,
        target_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[ModerationAction]:
        stmt = select(ModerationAction).where(ModerationAction.bot_id == bot_id)
        if group_id is not None:
            stmt = stmt.where(ModerationAction.group_id == group_id)
        if target_id is not None:
            stmt = stmt.where(ModerationAction.target_id == target_id)
        stmt = stmt.order_by(ModerationAction.created_at.desc()).limit(limit).offset(offset)
        return list((await self.session.scalars(stmt)).all())

    async def stats(self, bot_id: int, days: int = 7) -> dict[str, Any]:
        since = utcnow() - timedelta(days=days)
        rows = await self.session.execute(
            select(ModerationAction.action, func.count())
            .where(ModerationAction.bot_id == bot_id, ModerationAction.created_at >= since)
            .group_by(ModerationAction.action)
        )
        counts = {action: count for action, count in rows.all()}
        total = await self.session.scalar(
            select(func.count())
            .select_from(ModerationAction)
            .where(ModerationAction.bot_id == bot_id)
        )
        return {"by_action": counts, "total": int(total or 0), "days": days}


# --------------------------------------------------------------------------- #
# Activity / audit
# --------------------------------------------------------------------------- #
class ActivityEventService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        kind: str,
        title: str = "",
        bot_id: int | None = None,
        group_id: int | None = None,
        telegram_user_id: int | None = None,
        severity: str = "info",
        data: dict[str, Any] | None = None,
    ) -> ActivityEvent:
        event = ActivityEvent(
            bot_id=bot_id,
            group_id=group_id,
            kind=kind,
            title=title[:200],
            telegram_user_id=telegram_user_id,
            severity=severity,
            data=data or {},
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def feed(
        self, *, owner_bot_ids: Sequence[int], limit: int = 30
    ) -> Sequence[ActivityEvent]:
        if not owner_bot_ids:
            return []
        stmt = (
            select(ActivityEvent)
            .where(ActivityEvent.bot_id.in_(list(owner_bot_ids)))
            .order_by(ActivityEvent.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())

    async def series(self, bot_ids: Sequence[int], days: int = 7, kind_prefix: str | None = None) -> list[dict[str, Any]]:
        if not bot_ids:
            return []
        since = utcnow() - timedelta(days=days)
        stmt = (
            select(ActivityEvent.created_at, ActivityEvent.kind)
            .where(ActivityEvent.bot_id.in_(list(bot_ids)), ActivityEvent.created_at >= since)
        )
        if kind_prefix:
            stmt = stmt.where(ActivityEvent.kind.startswith(kind_prefix))
        rows = (await self.session.execute(stmt)).all()
        buckets: dict[str, int] = {}
        for created_at, _kind in rows:
            day = created_at.date().isoformat()
            buckets[day] = buckets.get(day, 0) + 1
        today = datetime.now(timezone.utc).date()
        series = []
        for offset in range(days - 1, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            series.append({"date": day, "count": buckets.get(day, 0)})
        return series


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log(
        self,
        action: str,
        *,
        actor_user_id: int | None = None,
        actor_type: str = "user",
        target_type: str = "",
        target_id: str | int = "",
        ip_address: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_user_id=actor_user_id,
            actor_type=actor_type,
            action=action[:80],
            target_type=target_type[:40],
            target_id=str(target_id)[:64],
            ip_address=ip_address[:64],
            metadata_json=metadata or {},
        )
        self.session.add(entry)
        await self.session.flush()
        return entry


# --------------------------------------------------------------------------- #
# Dashboard aggregate
# --------------------------------------------------------------------------- #
class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def overview(self, owner_id: int) -> dict[str, Any]:
        bots = list((await self.session.scalars(BotService(self.session)._owned(owner_id))).all())
        bot_ids = [b.id for b in bots]
        groups = 0
        plugins_enabled = 0
        moderation_total = 0
        if bot_ids:
            groups = int(
                await self.session.scalar(
                    select(func.count())
                    .select_from(BotGroup)
                    .where(BotGroup.bot_id.in_(bot_ids), BotGroup.is_active.is_(True))
                )
                or 0
            )
            plugins_enabled = int(
                await self.session.scalar(
                    select(func.count())
                    .select_from(PluginInstallation)
                    .where(
                        PluginInstallation.bot_id.in_(bot_ids),
                        PluginInstallation.state.in_(["enabled", "configured", "running"]),
                    )
                )
                or 0
            )
            moderation_total = int(
                await self.session.scalar(
                    select(func.count())
                    .select_from(ModerationAction)
                    .where(ModerationAction.bot_id.in_(bot_ids))
                )
                or 0
            )
        activity = await ActivityEventService(self.session).series(bot_ids, days=7)
        runbook = {
            "running": sum(1 for b in bots if b.status == "running"),
            "stopped": sum(1 for b in bots if b.status in ("stopped", "draft")),
            "error": sum(1 for b in bots if b.status == "error"),
        }
        return {
            "bots": {
                "total": len(bots),
                "clones": sum(1 for b in bots if b.is_clone),
                **runbook,
            },
            "groups": groups,
            "plugins_enabled": plugins_enabled,
            "moderation_actions": moderation_total,
            "activity_series": activity,
        }

    async def recent_activity(self, owner_id: int, limit: int = 20) -> list[dict[str, Any]]:
        bots = list((await self.session.scalars(BotService(self.session)._owned(owner_id))).all())
        events = await ActivityEventService(self.session).feed(
            owner_bot_ids=[b.id for b in bots], limit=limit
        )
        return [
            {
                "id": e.id,
                "kind": e.kind,
                "title": e.title,
                "severity": e.severity,
                "bot_id": e.bot_id,
                "group_id": e.group_id,
                "created_at": e.created_at.isoformat(),
                "data": e.data,
            }
            for e in events
        ]


# --------------------------------------------------------------------------- #
# Plugins
# --------------------------------------------------------------------------- #
PLUGIN_STATES = ("available", "installed", "enabled", "configured", "running")
VALID_TRANSITIONS: dict[str, set[str]] = {
    "available": {"installed"},
    "installed": {"enabled", "available", "running"},
    "enabled": {"configured", "installed", "running", "disabled"},
    "configured": {"running", "enabled", "disabled"},
    "running": {"disabled", "enabled", "configured"},
    "disabled": {"enabled", "installed"},
}


class PluginService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_plugin(self, slug: str) -> Plugin:
        plugin = await self.session.scalar(select(Plugin).where(Plugin.slug == slug))
        if plugin is None:
            raise NotFound(f"plugin {slug!r} not found")
        return plugin

    async def catalog(self) -> Sequence[Plugin]:
        return list(
            (await self.session.scalars(select(Plugin).order_by(Plugin.category, Plugin.name))).all()
        )

    async def installations_for_bot(self, bot_id: int) -> Sequence[PluginInstallation]:
        """Bot-scope rows plus the group rows and global rows that apply to it.

        A bot's effective plugin set is not one column: a plugin installed
        globally applies unless the bot overrides it, and a group row overrides
        both inside that group.  Callers that reconcile the runtime need all
        three sets, so this returns them together and lets
        :meth:`PluginManager.resolve_scope` decide precedence.
        """
        return list(
            (
                await self.session.scalars(
                    select(PluginInstallation)
                    .outerjoin(BotGroup, PluginInstallation.group_id == BotGroup.id)
                    .where(
                        (PluginInstallation.bot_id == bot_id)
                        | (PluginInstallation.bot_id.is_(None))
                        | (BotGroup.bot_id == bot_id)
                    )
                )
            ).all()
        )

    async def installation(
        self, slug: str, bot_id: int | None, group_id: int | None = None
    ) -> PluginInstallation | None:
        plugin = await self.get_plugin(slug)
        stmt = select(PluginInstallation).where(PluginInstallation.plugin_id == plugin.id)
        stmt = stmt.where(
            PluginInstallation.bot_id == bot_id if bot_id else PluginInstallation.bot_id.is_(None)
        )
        stmt = stmt.where(
            PluginInstallation.group_id == group_id
            if group_id
            else PluginInstallation.group_id.is_(None)
        )
        return await self.session.scalar(stmt)

    async def _check_dependencies(self, plugin: Plugin, bot_id: int | None) -> list[str]:
        missing: list[str] = []
        if not plugin.dependencies:
            return missing
        for slug in plugin.dependencies:
            install = await self.installation(slug, bot_id)
            if install is None or install.state not in ("enabled", "configured", "running"):
                missing.append(slug)
        return missing

    async def install(
        self, slug: str, *, bot_id: int | None, user_id: int | None, group_id: int | None = None,
        require_approved_version: bool = False,
    ) -> PluginInstallation:
        plugin = await self.get_plugin(slug)
        if plugin.status != "approved":
            raise ValidationError(f"plugin {slug} is not approved for installation")
        if require_approved_version:
            # A marketplace package must have an owner-approved, published,
            # pinned version before any bot may run it.  Approval is stored on the
            # version row, not inferred from the plugin record.
            from meiko.services.marketplace import MarketplaceService

            version = await MarketplaceService(self.session).installable_version(slug)
            if version is None:
                raise ValidationError(
                    f"plugin {slug} has no approved, published version; "
                    "the platform owner must approve it before installation"
                )
        existing = await self.installation(slug, bot_id, group_id)
        if existing:
            return existing
        missing = await self._check_dependencies(plugin, bot_id)
        if missing:
            raise ValidationError(
                "missing plugin dependencies: " + ", ".join(missing)
            )
        scope = "group" if group_id else ("bot" if bot_id else "global")
        installed_by = None
        if user_id is not None and await self.session.get(User, user_id) is not None:
            installed_by = user_id
        install = PluginInstallation(
            plugin_id=plugin.id,
            bot_id=bot_id,
            group_id=group_id,
            scope=scope,
            state="installed",
            version=plugin.version,
            config=defaults_from_schema(plugin.config_schema),
            installed_by=installed_by,
        )
        self.session.add(install)
        await self.session.flush()
        await self._record(
            install, "installed", "", "installed", actor_user_id=installed_by
        )
        return install

    async def transition(
        self, install: PluginInstallation, state: str, *, actor_user_id: int | None = None
    ) -> PluginInstallation:
        if state not in PLUGIN_STATES:
            raise ValidationError(f"unknown plugin state {state!r}")
        if install.state == state:
            return install
        allowed = VALID_TRANSITIONS.get(install.state, set())
        if state not in allowed:
            raise ValidationError(
                f"cannot move plugin from {install.state} to {state}"
            )
        previous = install.state
        install.state = state
        if state == "enabled":
            install.enabled_at = utcnow()
        await self.session.flush()
        await self._record(
            install, f"state.{state}", previous, state, actor_user_id=actor_user_id
        )
        return install

    async def _record(
        self,
        install: PluginInstallation,
        action: str,
        from_state: str = "",
        to_state: str = "",
        *,
        actor_user_id: int | None = None,
        message: str = "",
    ) -> None:
        """Append to the installation history.

        ``plugin_installations`` only knows the current state; this row is what makes
        "who enabled it, when, from which version" answerable after the fact.
        """
        from meiko.db.models import PluginInstallEvent

        self.session.add(
            PluginInstallEvent(
                installation_id=install.id,
                action=action[:24],
                from_state=(from_state or "")[:20],
                to_state=(to_state or install.state or "")[:20],
                version=(install.version or "")[:32],
                actor_user_id=actor_user_id,
                message=(message or "")[:1000],
            )
        )
        await self.session.flush()

    async def history(self, install: PluginInstallation) -> list[Any]:
        from meiko.db.models import PluginInstallEvent

        rows = (
            await self.session.scalars(
                select(PluginInstallEvent)
                .where(PluginInstallEvent.installation_id == install.id)
                .order_by(PluginInstallEvent.created_at.desc())
            )
        ).all()
        return list(rows)

    async def enable(
        self, install: PluginInstallation, *, actor_user_id: int | None = None
    ) -> PluginInstallation:
        """Enable an installation after verifying its required configuration.

        ``installed -> enabled`` always happens; when required config is missing
        the installation stays ``enabled`` but not ``configured`` and the caller
        receives a :class:`ValidationError` so it can prompt for the value.
        """
        plugin = await self.session.get(Plugin, install.plugin_id)
        assert plugin is not None
        missing = required_config_missing(plugin.config_schema, install.config or {})
        if install.state in ("installed", "disabled"):
            await self.transition(install, "enabled", actor_user_id=actor_user_id)
        if missing:
            raise ValidationError("missing required configuration: " + ", ".join(missing))
        if install.state == "enabled":
            await self.transition(install, "configured", actor_user_id=actor_user_id)
        return install

    async def disable(
        self, install: PluginInstallation, *, actor_user_id: int | None = None
    ) -> PluginInstallation:
        if install.state != "disabled":
            previous = install.state
            install.state = "disabled"
            install.enabled_at = None
            await self.session.flush()
            await self._record(
                install, "state.disabled", previous, "disabled", actor_user_id=actor_user_id
            )
        return install

    async def configure(
        self,
        install: PluginInstallation,
        values: dict[str, Any],
        *,
        actor_user_id: int | None = None,
    ) -> PluginInstallation:
        plugin = await self.session.get(Plugin, install.plugin_id)
        assert plugin is not None
        merged = dict(install.config or {})
        for key, value in values.items():
            if plugin.config_schema and key not in plugin.config_schema:
                raise ValidationError(f"unknown configuration key {key!r}")
            merged[key] = coerce_value(plugin.config_schema.get(key, {}), value)
        install.config = merged
        missing = required_config_missing(plugin.config_schema, merged)
        if not missing and install.state in ("installed", "enabled", "configured"):
            install.state = "configured"
        await self.session.flush()
        # Keys only: a history row must never store a secret the operator typed.
        await self._record(
            install,
            "configured",
            "",
            install.state,
            actor_user_id=actor_user_id,
            message="keys: " + ", ".join(sorted(values)),
        )
        return install

    async def uninstall(self, install: PluginInstallation) -> None:
        await self._record(install, "uninstalled", install.state, "")
        await self.session.delete(install)
        await self.session.flush()


def defaults_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, spec in (schema or {}).items():
        if isinstance(spec, dict) and "default" in spec:
            values[key] = spec["default"]
    return values


def required_config_missing(schema: dict[str, Any], config: dict[str, Any]) -> list[str]:
    missing = []
    for key, spec in (schema or {}).items():
        if not isinstance(spec, dict):
            continue
        if spec.get("required") and (key not in config or config[key] in (None, "")):
            missing.append(key)
    return missing


def coerce_value(spec: dict[str, Any], value: Any) -> Any:
    kind = (spec or {}).get("type", "string")
    try:
        if kind == "int":
            return int(value)
        if kind == "float":
            return float(value)
        if kind == "bool":
            if isinstance(value, str):
                return value.lower() in ("1", "true", "yes", "on")
            return bool(value)
        if kind == "list":
            if isinstance(value, str):
                return [v.strip() for v in value.split(",") if v.strip()]
            return list(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"invalid value for {kind}: {value!r}") from exc
    choices = (spec or {}).get("choices")
    if choices and value not in choices:
        raise ValidationError(f"value must be one of {', '.join(map(str, choices))}")
    return value


async def count_groups(session: AsyncSession, bot_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(BotGroup).where(BotGroup.bot_id == bot_id)
        )
        or 0
    )


async def purge_activity(session: AsyncSession, days: int = 90) -> int:
    """Delete activity rows older than ``days``. Kept for the cleanup job handler."""
    cutoff = utcnow() - timedelta(days=days)
    result = await session.execute(
        delete(ActivityEvent).where(ActivityEvent.created_at < cutoff)
    )
    return int(result.rowcount or 0)


async def run_retention(
    session: AsyncSession,
    *,
    activity_days: int = 90,
    command_log_days: int = 30,
    runtime_event_days: int = 30,
    plugin_log_days: int = 30,
    batch_size: int = 5000,
) -> dict[str, int]:
    """Delete old log rows, bounded per call.

    Every log table in this schema grows with usage and nothing ever deleted from
    them: ``purge_activity`` existed but no caller existed, and the command log, the
    runtime event feed and plugin logs had no retention at all.  On a managed
    database with a small disk that ends in a full volume, which fails *writes* — the
    bot keeps polling but stops recording, and eventually stops working.

    Deletion is batched so a first run against a large table does not hold a long
    transaction or a huge write lock; the job simply returns ``deleted < batch_size``
    when there is more to do and the next tick continues.
    """
    cutoff_activity = utcnow() - timedelta(days=activity_days)
    cutoff_logs = utcnow() - timedelta(days=command_log_days)
    cutoff_events = utcnow() - timedelta(days=runtime_event_days)
    cutoff_plugin = utcnow() - timedelta(days=plugin_log_days)

    removed: dict[str, int] = {}

    def _ids(model: Any, condition: Any) -> Any:
        return (
            select(model.id).where(condition).order_by(model.id).limit(batch_size).scalar_subquery()
        )

    plans = {
        "activity_events": (ActivityEvent, ActivityEvent.created_at < cutoff_activity),
        "command_executions": (
            CommandExecution,
            CommandExecution.created_at < cutoff_logs,
        ),
        "runtime_events": (RuntimeEvent, RuntimeEvent.created_at < cutoff_events),
        "plugin_logs": (PluginLog, PluginLog.created_at < cutoff_plugin),
    }
    for name, (model, condition) in plans.items():
        result = await session.execute(delete(model).where(model.id.in_(_ids(model, condition))))
        removed[name] = int(result.rowcount or 0)
    await session.flush()
    return removed


__all__ = [
    "ActivityEventService",
    "AuditService",
    "BotService",
    "Conflict",
    "DashboardService",
    "GroupService",
    "ModerationService",
    "NotFound",
    "PluginService",
    "PluginInstallation",
    "BotCommandRecord",
    "BotTemplate",
    "UserService",
    "ValidationError",
]
