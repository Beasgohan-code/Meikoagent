"""ORM models for the MEIKO AGENT platform.

The schema is the source of truth for ownership, plugin lifecycle, moderation
history, runtime state and auditing.  Designed to work on PostgreSQL and
SQLite alike (Alembic migrations target PostgreSQL first).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meiko.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- #
# Users & authentication
# --------------------------------------------------------------------------- #
class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str] = mapped_column(String(128), default="")
    photo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    language_code: Mapped[str] = mapped_column(String(16), default="en")
    role: Mapped[str] = mapped_column(String(20), default="user")  # owner/admin/user
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/restricted
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    bots: Mapped[list["BotAccount"]] = relationship(back_populates="owner")

    @property
    def display_name(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        return name or (f"@{self.username}" if self.username else f"User {self.id}")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="web")
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship()


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="default")
    prefix: Mapped[str] = mapped_column(String(16), default="mk")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------- #
# Bots
# --------------------------------------------------------------------------- #
class BotTemplate(TimestampMixin, Base):
    __tablename__ = "bot_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40), default="general")
    icon: Mapped[str] = mapped_column(String(64), default="robot")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    plugins: Mapped[list[str]] = mapped_column(JSON, default=list)
    commands: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class BotAccount(TimestampMixin, Base):
    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    telegram_bot_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(128), default="Meiko Bot")
    description: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    token_encrypted: Mapped[str] = mapped_column(Text, default="")
    token_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_valid: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    runtime_mode: Mapped[str] = mapped_column(String(20), default="polling")
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"), nullable=True)

    is_clone: Mapped[bool] = mapped_column(Boolean, default=False)
    source_bot_id: Mapped[int | None] = mapped_column(ForeignKey("bots.id"), nullable=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("bot_templates.id"), nullable=True)

    last_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    restart_count: Mapped[int] = mapped_column(Integer, default=0)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    owner: Mapped[User] = relationship(back_populates="bots")
    groups: Mapped[list["BotGroup"]] = relationship(back_populates="bot")


class BotGroup(TimestampMixin, Base):
    __tablename__ = "bot_groups"
    __table_args__ = (UniqueConstraint("bot_id", "chat_id", name="uq_bot_group"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chat_type: Mapped[str] = mapped_column(String(20), default="supergroup")
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    bot_status: Mapped[str] = mapped_column(String(20), default="member")  # member/administrator/left
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    bot: Mapped[BotAccount] = relationship(back_populates="groups")
    settings: Mapped["GroupSettings | None"] = relationship(
        back_populates="group", uselist=False, cascade="all, delete-orphan"
    )


class GroupSettings(TimestampMixin, Base):
    __tablename__ = "group_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), unique=True, index=True
    )
    welcome_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    welcome_text: Mapped[str] = mapped_column(Text, default="")
    welcome_buttons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    goodbye_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    goodbye_text: Mapped[str] = mapped_column(Text, default="")
    verification_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_delete_seconds: Mapped[int] = mapped_column(Integer, default=0)
    slow_mode_seconds: Mapped[int] = mapped_column(Integer, default=0)
    antispam: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    moderation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    logging_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    log_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    default_language: Mapped[str] = mapped_column(String(16), default="en")

    group: Mapped[BotGroup] = relationship(back_populates="settings")


class GroupMember(TimestampMixin, Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "telegram_id", name="uq_group_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), index=True
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    muted_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warnings_count: Mapped[int] = mapped_column(Integer, default=0)
    messages_count: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class GroupFilter(TimestampMixin, Base):
    __tablename__ = "group_filters"
    __table_args__ = (UniqueConstraint("group_id", "trigger", name="uq_group_filter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(128))
    response: Mapped[str] = mapped_column(Text, default="")
    match_type: Mapped[str] = mapped_column(String(20), default="exact")
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    buttons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# --------------------------------------------------------------------------- #
# Moderation
# --------------------------------------------------------------------------- #
class ModerationAction(Base):
    __tablename__ = "moderation_actions"
    __table_args__ = (Index("ix_moderation_group_created", "group_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    moderator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual/automation/plugin
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Warning(Base):
    __tablename__ = "warnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    moderator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cleared_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), nullable=True
    )
    reporter_id: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Appeal(Base):
    __tablename__ = "appeals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), nullable=True
    )
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), nullable=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decision: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class GroupRule(TimestampMixin, Base):
    """EVENT -> CONDITION -> ACTION -> AUDIT programmable group rules."""

    __tablename__ = "group_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    event: Mapped[str] = mapped_column(String(40), index=True)
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# --------------------------------------------------------------------------- #
# Plugins
# --------------------------------------------------------------------------- #
class Plugin(TimestampMixin, Base):
    __tablename__ = "plugins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    description: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(120), default="MEIKO AGENT")
    category: Mapped[str] = mapped_column(String(40), default="utilities", index=True)
    icon: Mapped[str] = mapped_column(String(64), default="plug")
    homepage: Mapped[str] = mapped_column(String(256), default="")
    license: Mapped[str] = mapped_column(String(64), default="MIT")
    source: Mapped[str] = mapped_column(String(20), default="builtin")  # builtin/marketplace/local
    status: Mapped[str] = mapped_column(String(20), default="approved")  # approved/pending/blocked
    is_official: Mapped[bool] = mapped_column(Boolean, default=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    config_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(128), default="")


class PluginInstallation(TimestampMixin, Base):
    __tablename__ = "plugin_installations"
    __table_args__ = (
        UniqueConstraint("plugin_id", "bot_id", "group_id", "scope", name="uq_plugin_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plugin_id: Mapped[int] = mapped_column(
        ForeignKey("plugins.id", ondelete="CASCADE"), index=True
    )
    bot_id: Mapped[int | None] = mapped_column(
        ForeignKey("bots.id", ondelete="CASCADE"), nullable=True, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), nullable=True
    )
    scope: Mapped[str] = mapped_column(String(20), default="bot")  # global/bot/group
    state: Mapped[str] = mapped_column(String(20), default="installed", index=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    installed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    plugin: Mapped[Plugin] = relationship()


class PluginLog(Base):
    __tablename__ = "plugin_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    installation_id: Mapped[int] = mapped_column(
        ForeignKey("plugin_installations.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# --------------------------------------------------------------------------- #
# Marketplace: source -> package -> version -> approval -> install -> activation
# --------------------------------------------------------------------------- #
# These are deliberately *four* tables plus the existing installation row.  One
# "plugin" column could not express "the owner approved v1.2 at commit abc123
# from this source, but the bot is still running v1.1", which is the question an
# operator actually asks.  Nothing here executes code: a source is a record, a
# package is a manifest, a version is an identified artefact.
class PluginSource(TimestampMixin, Base):
    """An owner-registered git repository the marketplace may import from."""

    __tablename__ = "plugin_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(512), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="git")  # git | local
    #: Branch/tag the owner pinned.  A commit is resolved at import time and
    #: stored on the version row: "latest" is never re-resolved implicitly.
    ref: Mapped[str] = mapped_column(String(120), default="main")
    license: Mapped[str] = mapped_column(String(64), default="")
    license_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    trust: Mapped[str] = mapped_column(String(20), default="untrusted")  # trusted | untrusted
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PluginPackage(TimestampMixin, Base):
    """A plugin identity discovered inside a source (slug + manifest path)."""

    __tablename__ = "plugin_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("plugin_sources.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    path: Mapped[str] = mapped_column(String(256), default="")
    entrypoint: Mapped[str] = mapped_column(String(256), default="")
    #: AVAILABLE -> REVIEW_PENDING -> APPROVED -> PLATFORM_INSTALLED -> REMOVED
    state: Mapped[str] = mapped_column(String(24), default="available", index=True)
    blocked_reason: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_notes: Mapped[str] = mapped_column(Text, default="")


class PluginVersion(TimestampMixin, Base):
    """One pinned, checksummed artefact of a package."""

    __tablename__ = "plugin_versions"
    __table_args__ = (UniqueConstraint("package_id", "version", name="uq_package_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("plugin_packages.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(32))
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    checksum: Mapped[str] = mapped_column(String(128), default="")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: pending | approved | rejected | withdrawn
    approval: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    #: Findings from the inspection pipeline, kept verbatim for the audit trail.
    inspection: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    licence: Mapped[str] = mapped_column(String(64), default="")
    #: A version may only be installed as a platform package once this is true.
    published: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------- #
# Commands, automation, scheduling
# --------------------------------------------------------------------------- #
class BotCommandRecord(TimestampMixin, Base):
    __tablename__ = "bot_commands"
    __table_args__ = (UniqueConstraint("bot_id", "name", name="uq_bot_command"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(256), default="")
    category: Mapped[str] = mapped_column(String(40), default="general")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    arguments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    permission: Mapped[str] = mapped_column(String(24), default="everyone")
    scope: Mapped[str] = mapped_column(String(16), default="all")  # all/private/group
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    handler_type: Mapped[str] = mapped_column(String(20), default="builtin")  # builtin/plugin/declarative
    handler_ref: Mapped[str] = mapped_column(String(160), default="")
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)


class CommandExecution(Base):
    """One recorded command invocation, for the Command Studio log.

    Written by the bot process, read by the webapp.  A row means "the dispatcher
    saw this command for this bot at this time"; a *manual test* from the Studio
    is recorded with ``source="studio"`` and never actually sent to Telegram.
    """

    __tablename__ = "command_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        ForeignKey("bots.id", ondelete="CASCADE"), index=True
    )
    command: Mapped[str] = mapped_column(String(64), index=True)
    chat_type: Mapped[str] = mapped_column(String(20), default="")
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_role: Mapped[str] = mapped_column(String(24), default="")
    #: ok | denied | error | unknown
    result: Mapped[str] = mapped_column(String(16), default="ok", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="telegram")  # telegram | studio
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Workflow(TimestampMixin, Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ScheduledTask(TimestampMixin, Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(
        ForeignKey("bots.id", ondelete="CASCADE"), nullable=True, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), nullable=True
    )
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(120), default="task")
    kind: Mapped[str] = mapped_column(String(24), default="announcement")
    cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------- #
# Runtime, infrastructure, observability
# --------------------------------------------------------------------------- #
class Worker(TimestampMixin, Base):
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    hostname: Mapped[str] = mapped_column(String(120), default="")
    pid: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="starting", index=True)
    capacity: Mapped[int] = mapped_column(Integer, default=10)
    current_load: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RuntimeEvent(Base):
    __tablename__ = "runtime_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(
        ForeignKey("bots.id", ondelete="CASCADE"), nullable=True, index=True
    )
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(20), default="user")  # user/api/bot/system
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(40), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Webhook(TimestampMixin, Base):
    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(512))
    secret_encrypted: Mapped[str] = mapped_column(Text, default="")
    events: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    webhook_id: Mapped[int] = mapped_column(
        ForeignKey("webhooks.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(60))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_preview: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey("bots.id"), nullable=True, index=True)
    metric: Mapped[str] = mapped_column(String(40), index=True)  # messages/commands/api_calls
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ActivityEvent(Base):
    """Denormalised, indexed activity feed powering dashboards and charts."""

    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(
        ForeignKey("bots.id", ondelete="CASCADE"), nullable=True, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
