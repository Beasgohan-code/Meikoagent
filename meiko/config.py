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

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

BOT_API_VERSION = "10.4"


#: Schemes a hosting provider or a dashboard actually gives you, and the async
#: driver that works with this codebase.  Pasting a copied URL must work: both
#: Render and Supabase hand out plain ``postgresql://`` strings, and SQLAlchemy then
#: selects the *synchronous* psycopg driver, which is neither installed here nor
#: usable from async code — the deployment dies at boot with ModuleNotFoundError.
_SYNC_POSTGRES_SCHEMES = ("postgresql", "postgres")
_ASYNC_POSTGRES_SCHEME = "postgresql+asyncpg"


def normalize_async_database_url(url: str) -> str:
    """Map a provider's connection string onto the driver this app uses."""
    if not url:
        return url
    scheme, separator, rest = url.partition("://")
    if separator and scheme.lower() in _SYNC_POSTGRES_SCHEMES:
        return f"{_ASYNC_POSTGRES_SCHEME}://{rest}"
    return url


_LOCAL_DB_HOSTS = ("localhost", "127.0.0.1", "::1", "db", "postgres", "host.docker.internal")


def database_tls_warning(settings: "Settings") -> str | None:
    """Return a message when a remote Postgres connection is not using TLS.

    Managed providers (Supabase, Neon, Render, RDS) refuse or downgrade non-TLS
    connections, and the failure surfaces as a connection reset in the middle of a
    query rather than as a configuration error.  Returned in development, raised in
    production by :func:`meiko.api.app._verify_deployment_settings`.
    """
    url = settings.resolved_database_url
    if not url.startswith(("postgresql", "postgres")):
        return None
    lowered = url.lower()
    if "sslmode=" in lowered or "ssl=" in lowered:
        return None
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:  # pragma: no cover
        return None
    if not host or host in _LOCAL_DB_HOSTS or host.startswith("127."):
        return None
    return (
        f"database host {host!r} is remote but the URL does not request TLS; add "
        "?sslmode=require (managed Postgres such as Supabase/Neon/Render requires it)"
    )


def migration_url_problem(settings: "Settings") -> str | None:
    """Return a message when migrations would run against a transaction pooler."""
    if not settings.database_is_pooled or settings.migration_url:
        return None
    return (
        "DATABASE_URL points at a pooler (transaction mode) and MIGRATION_URL is empty: "
        "multi-statement DDL is not safe there. Set MIGRATION_URL to the provider's "
        "direct/session connection (Supabase: port 5432 on db.<ref>.supabase.co) and run "
        "`python start.py --service migrate` before starting the API"
    )


class Settings(BaseSettings):
    """Platform wide settings, loaded from environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=os.getenv("MEIKO_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def _honour_platform_port(self) -> "Settings":
        """Take ``$PORT`` when the host assigns one (Render, Railway, Heroku, Koyeb).

        These platforms route traffic to a single port and probe it for health.  An
        API listening on 8000 while the platform expects 10000 is marked unhealthy and
        restarted in a loop - which looks exactly like "keep-alive is broken".
        An explicit ``API_PORT`` still wins.
        """
        platform_port = os.getenv("PORT", "").strip()
        if platform_port.isdigit() and os.getenv("API_PORT", "").strip() == "":
            object.__setattr__(self, "api_port", int(platform_port))
        return self

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
    #: Long-poll timeout for bot workers.  Liveness is measured against a multiple
    #: of this, so raising it widens the window before a bot is called stale.
    telegram_polling_timeout: int = 30

    # ------------------------------------------------------------- retention
    #: Log tables grow with every command, event and plugin log line. Nothing used to
    #: delete from them, which fills a small managed database and then fails writes.
    retention_enabled: bool = True
    retention_interval_seconds: int = 6 * 60 * 60
    retention_activity_days: int = 90
    retention_command_log_days: int = 30
    retention_runtime_event_days: int = 30
    retention_plugin_log_days: int = 30
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
            return normalize_async_database_url(self.database_url)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{(self.data_dir / 'meiko.db').as_posix()}"

    @property
    def database_is_pooled(self) -> bool:
        """True for a transaction pooler (Supabase pgbouncer, pgBouncer, RDS proxy)."""
        from urllib.parse import urlsplit

        url = self.resolved_database_url
        lowered = url.lower()
        if "pooler" in lowered or "pgbouncer" in lowered:
            return True
        try:
            return urlsplit(url).port == 6543
        except ValueError:  # pragma: no cover
            return False

    @property
    def migration_database_url(self) -> str:
        """The URL Alembic should use.

        A Pooled connection (Supabase's pgbouncer, port 6543, transaction mode)
        cannot run multi-statement DDL reliably, so migrations take a direct URL
        when one is configured and fall back to the app URL otherwise.
        """
        if self.migration_url:
            return normalize_async_database_url(self.migration_url)
        return self.resolved_database_url

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
