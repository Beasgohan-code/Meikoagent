"""Alembic environment: async engine, settings driven URL, metadata autogenerate."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from meiko.config import get_settings
from meiko.db.base import Base
from meiko.db import models  # noqa: F401  (register all tables)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
# Alembic uses the direct URL when one is configured: a transaction-mode pooler
# (Supabase pgbouncer) cannot run multi-statement DDL reliably.
config.set_main_option("sqlalchemy.url", settings.migration_database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.migration_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
        # ``--sql`` mode has no live database, so a batch alter (SQLite cannot add
        # constraints in place) cannot reflect the existing table.  Rendering needs
        # the model definition instead, which is what copy_from supplies.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def include_object(obj, name, type_, reflected, compare_to):
    """Keep autogenerate honest about what SQLite can actually store.

    SQLite cannot add a foreign key to an existing table, so a constraint declared
    in the models but created only on PostgreSQL would otherwise show up as a
    permanently-pending migration on every development database (and fail
    ``alembic check``).  Filtering those diffs means "no new operations" on SQLite
    reflects reality: the column and index exist everywhere, the FK exists on the
    dialect that can hold it.
    """
    if type_ in ("foreign_key_constraint", "foreign_key"):
        # The FK is present in the models on every dialect but only creatable on
        # PostgreSQL; see the note in revision 404947bb2f88.
        return False
    return True


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
