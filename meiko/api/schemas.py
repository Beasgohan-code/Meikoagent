"""Pydantic request/response schemas for the public API (v1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorResponse(BaseModel):
    error: str
    message: str
    detail: dict[str, Any] | None = None


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int
    has_more: bool


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class TelegramLoginRequest(BaseModel):
    id: int
    auth_date: int
    hash: str
    first_name: str = ""
    last_name: str = ""
    username: str | None = None
    photo_url: str | None = None
    bot_token: str | None = Field(default=None, description="Optional token override (owner only)")


class WebAppLoginRequest(BaseModel):
    init_data: str = Field(min_length=10, max_length=8192)
    bot_token: str | None = None


class DevLoginRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    username: str | None = None
    first_name: str = "Developer"


class SessionResponse(BaseModel):
    token: str
    expires_in: int
    user: "UserResponse"


class UserResponse(ORMModel):
    id: int
    telegram_id: int | None
    username: str | None
    first_name: str
    last_name: str
    photo_url: str | None
    role: str
    status: str
    is_owner: bool
    created_at: datetime
    last_seen_at: datetime | None


# --------------------------------------------------------------------------- #
# Bots
# --------------------------------------------------------------------------- #
class BotCreateRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    name: str | None = Field(default=None, max_length=128)
    template_slug: str | None = None
    runtime_mode: Literal["polling", "webhook"] = "polling"


class BotCloneRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    name: str | None = Field(default=None, max_length=128)
    copy_plugins: bool = True


class BotUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    runtime_mode: Literal["polling", "webhook"] | None = None
    settings: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None


class BotResponse(ORMModel):
    id: int
    owner_id: int
    telegram_bot_id: int | None
    username: str | None
    name: str
    description: str
    avatar_url: str | None
    status: str
    runtime_mode: str
    token_valid: bool
    is_clone: bool
    source_bot_id: int | None
    last_started_at: datetime | None
    last_stopped_at: datetime | None
    last_error: str | None
    restart_count: int
    created_at: datetime
    settings: dict[str, Any] = {}
    limits: dict[str, Any] = {}


class TemplateResponse(ORMModel):
    id: int
    slug: str
    name: str
    description: str
    category: str
    icon: str
    plugins: list[str] = []
    commands: list[str] = []
    config: dict[str, Any] = {}
    is_public: bool


class BotHealthResponse(BaseModel):
    bot_id: int
    status: str
    mode: str
    running: bool
    uptime_seconds: int = 0
    plugin_count: int = 0
    last_error: str | None = None
    worker: dict[str, Any] | None = None
    available: bool = True
    note: str | None = None


# --------------------------------------------------------------------------- #
# Plugins
# --------------------------------------------------------------------------- #
class PluginResponse(ORMModel):
    id: int
    slug: str
    name: str
    version: str
    description: str
    author: str
    category: str
    icon: str
    source: str
    status: str
    is_official: bool
    permissions: list[str] = []
    dependencies: list[str] = []
    config_schema: dict[str, Any] = {}


class PluginInstallRequest(BaseModel):
    bot_id: int | None = None
    group_id: int | None = None


class PluginConfigureRequest(BaseModel):
    config: dict[str, Any]


class PluginInstallationResponse(ORMModel):
    id: int
    plugin_id: int
    bot_id: int | None
    group_id: int | None
    scope: str
    state: str
    version: str
    config: dict[str, Any] = {}
    enabled_at: datetime | None
    last_error: str | None


# --------------------------------------------------------------------------- #
# Groups & moderation
# --------------------------------------------------------------------------- #
class GroupResponse(ORMModel):
    id: int
    bot_id: int
    chat_id: int
    title: str
    username: str | None
    chat_type: str
    member_count: int
    bot_status: str
    is_active: bool
    last_activity_at: datetime | None


class GroupSettingsUpdate(BaseModel):
    welcome_enabled: bool | None = None
    welcome_text: str | None = Field(default=None, max_length=3000)
    goodbye_enabled: bool | None = None
    goodbye_text: str | None = Field(default=None, max_length=3000)
    verification_enabled: bool | None = None
    auto_delete_seconds: int | None = Field(default=None, ge=0, le=86400)
    slow_mode_seconds: int | None = Field(default=None, ge=0, le=3600)
    antispam: dict[str, Any] | None = None
    moderation: dict[str, Any] | None = None
    logging_enabled: bool | None = None


class ModerationActionResponse(ORMModel):
    id: int
    bot_id: int
    group_id: int | None
    chat_id: int | None
    target_id: int | None
    moderator_id: int | None
    action: str
    reason: str
    duration_seconds: int
    source: str
    created_at: datetime


class RuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    event: str = Field(default="message", max_length=40)
    conditions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    priority: int = 100
    enabled: bool = True
    group_id: int | None = None

    @field_validator("actions")
    @classmethod
    def _actions_not_empty(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not value:
            raise ValueError("a rule needs at least one action")
        return value


class RuleResponse(ORMModel):
    id: int
    bot_id: int
    group_id: int | None
    name: str
    event: str
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    priority: int
    enabled: bool


# --------------------------------------------------------------------------- #
# Commands & automation
# --------------------------------------------------------------------------- #
class CommandRequest(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=256)
    category: str = Field(default="general", max_length=40)
    aliases: list[str] = []
    permission: str = "everyone"
    scope: Literal["all", "private", "group"] = "all"
    enabled: bool = True
    handler_type: Literal["builtin", "plugin", "declarative"] = "declarative"
    handler_ref: str = ""
    definition: dict[str, Any] = {}

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        import re

        if not re.match(r"^[a-z0-9_]{1,32}$", value):
            raise ValueError("use lowercase letters, digits and underscores")
        return value


class CommandResponse(ORMModel):
    id: int
    bot_id: int
    name: str
    description: str
    category: str
    aliases: list[str]
    permission: str
    scope: str
    enabled: bool
    handler_type: str
    handler_ref: str
    definition: dict[str, Any]
    usage_count: int


class WorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    trigger: dict[str, Any] = {}
    conditions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []


class WorkflowResponse(ORMModel):
    id: int
    bot_id: int
    name: str
    description: str
    enabled: bool
    trigger: dict[str, Any]
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    version: int
    run_count: int
    last_run_at: datetime | None


class ScheduleRequest(BaseModel):
    name: str = "announcement"
    kind: Literal["announcement", "workflow", "cleanup", "custom"] = "announcement"
    group_id: int | None = None
    cron: str | None = Field(default=None, max_length=64)
    run_at: datetime | None = None
    payload: dict[str, Any] = {}


class ScheduleResponse(ORMModel):
    id: int
    bot_id: int | None
    name: str
    kind: str
    cron: str | None
    next_run_at: datetime | None
    status: str
    attempts: int
    last_error: str | None


# --------------------------------------------------------------------------- #
# Developer & infrastructure
# --------------------------------------------------------------------------- #
class ApiKeyCreateRequest(BaseModel):
    name: str = Field(default="default", max_length=120)
    scopes: list[str] = []


class ApiKeyResponse(ORMModel):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreatedResponse(BaseModel):
    key: str
    record: ApiKeyResponse
    note: str = "Store this key now – it is shown only once."


class WebhookRequest(BaseModel):
    url: str = Field(min_length=8, max_length=512)
    events: list[str] = ["moderation", "activity"]
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("webhook URL must be http(s)")
        return value


class WebhookResponse(ORMModel):
    id: int
    bot_id: int
    url: str
    events: list[str]
    enabled: bool
    failure_count: int
    last_delivery_at: datetime | None
    last_status: int | None


class RuntimeHealthResponse(BaseModel):
    status: str
    services: dict[str, Any]
    version: str
    bot_api: str
    timestamp: datetime


class ActivityItem(ORMModel):
    id: int
    kind: str
    title: str
    severity: str
    bot_id: int | None
    group_id: int | None
    created_at: datetime
    data: dict[str, Any]


class DashboardResponse(BaseModel):
    bots: dict[str, int]
    groups: int
    plugins_enabled: int
    moderation_actions: int
    activity_series: list[dict[str, Any]]


SessionResponse.model_rebuild()
