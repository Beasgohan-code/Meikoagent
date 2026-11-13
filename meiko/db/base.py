"""Database layer: async SQLAlchemy 2.0 engine/session management.

PostgreSQL (asyncpg) is the production target while SQLite (aiosqlite) keeps
local development, the sandbox and the test-suite fully functional.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from meiko.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for every Meiko model."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _engine_kwargs(settings: Settings, url: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"echo": settings.db_echo, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30}
    else:
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return kwargs


def create_engine(settings: Settings | None = None, url: str | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    database_url = url or settings.resolved_database_url
    engine = create_async_engine(database_url, **_engine_kwargs(settings, database_url))
    if database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


def init_engine(settings: Settings | None = None, url: str | None = None) -> AsyncEngine:
    """Initialise the process wide engine (idempotent)."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine
    settings = settings or get_settings()
    _engine = create_engine(settings, url)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        return init_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session scope used by services, workers and the API."""
    factory = get_sessionmaker()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def database_health() -> dict[str, Any]:
    """Real connectivity probe, never a fake 200."""
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        backend = engine.dialect.name
        pool = getattr(engine, "pool", None)
        stats: dict[str, Any] = {"dialect": backend}
        if pool is not None and hasattr(pool, "status"):
            try:
                stats["pool"] = str(pool.status())  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover
                pass
        return {"status": "ok", **stats}
    except Exception as exc:  # pragma: no cover - depends on external state
        logger.warning("database health check failed: %s", exc)
        return {"status": "error", "error": str(exc)}


class SchemaMismatch(RuntimeError):
    """The database is missing columns the models require."""


def missing_schema_objects(connection: Any) -> list[str]:
    """Tables and columns the models need but the database does not have.

    ``Base.metadata.create_all`` creates missing *tables* and silently ignores
    existing ones, so a deployment that upgrades against an existing database gets
    a schema that is half old and half new: nothing fails at boot, and then every
    query mentioning a new column returns 500.  This reports exactly what is
    missing so startup can fail loudly, or so a developer can be told to migrate.
    """
    from sqlalchemy import inspect

    inspector = inspect(connection)
    present_tables = set(inspector.get_table_names())
    problems: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in present_tables:
            problems.append(f"table {table_name}")
            continue
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in actual:
                problems.append(f"{table_name}.{column.name}")
    return problems


async def create_all() -> None:
    """Create missing tables, then verify the schema the models actually need.

    With ``AUTO_CREATE_SCHEMA=false`` (containers) the schema is owned solely by
    Alembic, so nothing is created here and startup never races the migrate
    service — but the verification still runs, because a mismatch found at boot is
    a five-second fix and a mismatch found as a 500 in production is not.

    Creating a missing table is safe; adding a missing column is not, because only
    Alembic knows how to backfill.  So this function never alters a table: it
    creates what does not exist and raises :class:`SchemaMismatch` for the rest.
    """
    from meiko.config import get_settings
    from meiko.db import models  # noqa: F401  (register metadata)

    settings = get_settings()
    engine = get_engine()
    async with engine.begin() as conn:
        if settings.auto_create_schema:
            await conn.run_sync(Base.metadata.create_all)
        missing = await conn.run_sync(missing_schema_objects)

    if not missing:
        return

    if settings.auto_create_schema and settings.database_url.startswith("sqlite"):
        # The sandbox and test databases are disposable; reconcile them so a local
        # checkout never carries a stale schema from an earlier revision.
        await _rebuild_sqlite_schema()
        return

    raise SchemaMismatch(
        "the database is missing "
        + ", ".join(missing[:10])
        + ("…" if len(missing) > 10 else "")
        + "; run `python start.py --service migrate` (or `alembic upgrade head`) "
        "before starting the API"
    )


async def _rebuild_sqlite_schema() -> None:
    """Drop and recreate a disposable SQLite schema that no longer matches.

    Only reachable with ``AUTO_CREATE_SCHEMA=1`` on SQLite — local development and
    the sandbox.  It is deliberately not silent: the data loss is logged.
    """
    logger.warning(
        "SQLite schema does not match the models and AUTO_CREATE_SCHEMA is on; "
        "recreating it (local data will be lost)"
    )
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
