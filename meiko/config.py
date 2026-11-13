"""Unified configuration for the MEIKO AGENT platform.

One settings object is shared by the bot runtime, the API control plane and the
background services.  Everything is environment driven so that the platform can
run identically on a developer laptop (SQLite + in-memory cache) and in Docker
(PostgreSQL + Redis).
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

BOT_API_VERSION = "10.4"


class Settings(BaseSettings):
    """Platform wide settings, loaded from environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=os.getenv("MEIKO_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ core
    app_name: str = "MEIKO AGENT"
    environment: Literal["development", "testing", "production"] = "development"
    debug: bool = False
    #: Signing key for sessions.  A random default is fine for one process, but it
    #: changes on every restart and on every *worker*, so a multi-process
    #: deployment must set it — sessions would otherwise die on restart and tokens
    #: issued by one worker would be rejected by another. ``is_production`` makes
    #: that a boot failure instead of a mystery.
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    api_prefix: str = "/api/v1"
    #: When false the schema is owned exclusively by Alembic (containers set
    #: ``AUTO_CREATE_SCHEMA=false`` and run ``--service migrate`` first).
    auto_create_schema: bool = True

    # ------------------------------------------------------------ module data
    #: Where module documents live. Empty = ``<data_dir>/modules`` on disk.
    #: ``supabase://<project-ref>/<bucket>`` keeps them in Storage instead, which
    #: is what a serverless deployment needs (no durable local filesystem).
    module_store_url: str = ""
    module_store_token: str = ""
    #: Optional override for self-hosted Storage (defaults to the project host).
    module_store_base_url: str = ""

    # -------------------------------------------------------------- database
    database_url: str = ""
    #: Direct (non-pooled) URL for Alembic; empty = reuse ``database_url``.
    migration_url: str = ""
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ----------------------------------------------------------------- redis
    redis_url: str = ""
    redis_namespace: str = "meiko"
    cache_ttl_seconds: int = 60

    # ------------------------------------------------------------ encryption
    # Fernet key protecting bot tokens at rest.  Generated on first boot when
    # empty and persisted to ``data/.encryption_key`` (never committed).
    encryption_key: str = ""

    # --------------------------------------------------------------- telegram
    bot_token: str = ""
    owner_telegram_ids: str = ""
    webhook_url: str = ""
    webhook_secret: str = ""
    polling_enabled: bool = True
    telegram_api_base: str = "https://api.telegram.org"

    # ---------------------------------------------------------------- runtime
    data_dir: Path = DEFAULT_DATA_DIR
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"
    run_main_bot: bool = True
    max_clone_workers: int = 25
    clone_heartbeat_seconds: int = 20
    clone_crash_loop_threshold: int = 5
    clone_crash_loop_window_seconds: int = 300

    # -------------------------------------------------------------------- api
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    session_ttl_seconds: int = 60 * 60 * 24 * 14
    auth_dev_mode: bool = True

    # ------------------------------------------------------------------ misc
    maintenance_mode: bool = False
    scheduler_enabled: bool = True
    watchdog_enabled: bool = True
    watchdog_interval_seconds: int = 30
    rate_limit_per_minute: int = 120
    #: When true, a configured-but-unreachable Redis is a boot failure instead of
    #: a silent downgrade to per-process memory.  Implied by ENVIRONMENT=production.
    require_redis: bool = False
    #: Extra origins the browser may POST from.  Same-origin is always allowed.
    csrf_enforce: bool = True
    #: Force the ``Secure`` flag on the session cookie (implied by production, or
    #: enable it when TLS terminates in front of a non-production deployment).
    cookie_secure: bool = False

    # ---------------------------------------------------------------- plugins
    #: Global-scope plugin installations are a platform-owner capability.  The
    #: flag exists so an owner can switch the capability off completely (e.g. on
    #: a single-tenant deployment) without editing code; it never *grants*
    #: anything to a non-owner.
    plugin_global_scope_enabled: bool = True

    # ------------------------------------------------------------ derived ----
    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand_data_dir(cls, value: object) -> object:
        if isinstance(value, str) and value:
            return Path(value).expanduser()
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_testing(self) -> bool:
        return self.environment == "testing"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{(self.data_dir / 'meiko.db').as_posix()}"

    @property
    def migration_database_url(self) -> str:
        """The URL Alembic should use.

        A Pooled connection (Supabase's pgbouncer, port 6543, transaction mode)
        cannot run multi-statement DDL reliably, so migrations take a direct URL
        when one is configured and fall back to the app URL otherwise.
        """
        return self.migration_url or self.resolved_database_url

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def owner_ids(self) -> set[int]:
        ids: set[int] = set()
        for chunk in self.owner_telegram_ids.replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                ids.add(int(chunk))
        return ids

    @property
    def lock_dir(self) -> Path:
        path = self.data_dir / "locks"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_dirs(self) -> None:
        for name in ("", "logs", "plugins", "tmp", "backups", "locks"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reload_settings() -> Settings:
    """Drop the cache – used by tests and the owner control center."""
    get_settings.cache_clear()
    return get_settings()
