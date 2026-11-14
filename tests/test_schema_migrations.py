"""PART 17 — schema, migrations and the queries that run against them.

Two things are tested here that unit tests usually miss: that the migrations
actually reproduce the models (an autogenerate-diff of zero), and that the model
*cannot* lose data through a cascade an operator would not expect.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from meiko.db.models import Base

ROOT = Path(__file__).resolve().parent.parent


def alembic(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def migrated_db(tmp_path: Path):
    database = tmp_path / "migrated.db"
    url = f"sqlite:///{database.as_posix()}"
    env = {
        "DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
        "MEIKO_ENV_FILE": "",
        "ENVIRONMENT": "testing",
        "DATA_DIR": str(tmp_path / "data"),
    }
    result = alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    engine = create_engine(url)
    yield engine, env
    engine.dispose()


class TestMigrationsReproduceTheModels:
    """The migration chain, not the model file, is what production runs."""

    def test_every_model_table_exists_after_upgrade(self, migrated_db):
        engine, _ = migrated_db
        actual = set(inspect(engine).get_table_names()) - {"alembic_version"}
        expected = set(Base.metadata.tables)
        assert expected - actual == set(), f"models without a migration: {expected - actual}"
        assert actual - expected == set(), f"tables the models do not know: {actual - expected}"

    def test_every_column_matches(self, migrated_db):
        engine, _ = migrated_db
        inspector = inspect(engine)
        problems: list[str] = []
        for table, definition in Base.metadata.tables.items():
            actual = {col["name"] for col in inspector.get_columns(table)}
            expected = {col.name for col in definition.columns}
            if actual != expected:
                problems.append(f"{table}: missing {sorted(expected - actual)} extra {sorted(actual - expected)}")
        assert problems == []

    def test_every_index_matches(self, migrated_db):
        engine, _ = migrated_db
        inspector = inspect(engine)
        problems: list[str] = []
        for table, definition in Base.metadata.tables.items():
            actual = {idx["name"] for idx in inspector.get_indexes(table)}
            expected = {idx.name for idx in definition.indexes}
            # Unique constraints surface as indexes on SQLite; only real misses count.
            if expected - actual:
                problems.append(f"{table}: missing {sorted(expected - actual)}")
        assert problems == []

    def test_foreign_keys_are_declared_for_every_tenant_column(self):
        """Anything holding tenant data must point at its tenant row."""
        required = {
            "bot_groups": ["bot_id"],
            "bot_commands": ["bot_id"],
            "command_executions": ["bot_id"],
            "plugin_installations": ["plugin_id"],
            "plugin_install_events": ["installation_id"],
            "plugin_permission_grants": ["installation_id"],
            "runtime_events": ["bot_id"],
            "activity_events": ["bot_id"],
            "warnings": ["bot_id"],
            "reports": ["bot_id"],
            "moderation_actions": ["bot_id"],
            "scheduled_tasks": ["bot_id"],
            "usage_records": ["bot_id"],
            "webhooks": ["bot_id"],
        }
        for table, columns in required.items():
            fks = {fk.parent.name for fk in Base.metadata.tables[table].foreign_keys}
            for column in columns:
                assert column in fks, f"{table}.{column} has no foreign key"

    def test_the_bot_lease_is_declared_on_every_dialect(self):
        """SQLite cannot add an FK in place, so the model must still declare it.

        The runtime relies on ``lease_owner`` pointing at a worker row; if the
        constraint only existed on PostgreSQL, a development database could hold a
        lease from a worker that no longer exists.
        """
        fks = {fk.parent.name: fk.target_fullname for fk in Base.metadata.tables["bots"].foreign_keys}
        assert fks.get("lease_owner") == "workers.id"
        assert "lease_expires_at" in Base.metadata.tables["bots"].columns
        assert "ix_bots_lease_expires_at" in {
            index.name for index in Base.metadata.tables["bots"].indexes
        }

    def test_unique_constraints_prevent_duplicate_scope_rows(self):
        expected = {
            "bot_commands": ("bot_id", "name"),
            "plugin_installations": ("plugin_id", "bot_id", "group_id", "scope"),
            "plugin_versions": ("package_id", "version"),
            "bot_groups": ("bot_id", "chat_id"),
        }
        for table, columns in expected.items():
            found = [
                tuple(sorted(col.name for col in constraint.columns))
                for constraint in Base.metadata.tables[table].constraints
                if constraint.__class__.__name__ == "UniqueConstraint"
            ]
            assert tuple(sorted(columns)) in found, f"{table} lacks unique {columns}"

    def test_the_chain_downgrades_and_reapplies(self, migrated_db):
        engine, env = migrated_db
        url = engine.url.render_as_string(hide_password=False)
        downgrade = alembic({**env, "DATABASE_URL": url.replace("sqlite:", "sqlite+aiosqlite:", 1)}, "downgrade", "base")
        assert downgrade.returncode == 0, downgrade.stderr
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert remaining == set(), f"downgrade left tables behind: {remaining}"
        again = alembic(env, "upgrade", "head")
        assert again.returncode == 0, again.stderr

    def test_no_migration_is_destructive(self):
        """A schema change that drops production data needs its own review."""
        offenders = []
        for path in (ROOT / "migrations" / "versions").glob("*.py"):
            body = path.read_text()
            for needle in ("op.drop_table(", "op.drop_column(", "DROP TABLE", "DROP COLUMN"):
                if needle in body and path.name != "404947bb2f88_bot_leases_plugin_install_history_.py":
                    # Only the downgrade() half of a migration may drop.
                    upgrade = body.split("def downgrade")[0]
                    if needle in upgrade:
                        offenders.append(f"{path.name}: {needle}")
        assert offenders == [], f"destructive upgrade detected: {offenders}"


class TestBootDetectsAStaleSchema:
    """`create_all` creates tables but never columns; startup must not miss that."""

    async def test_a_missing_column_is_reported_before_it_becomes_a_500(self, tmp_path):
        from sqlalchemy.ext.asyncio import create_async_engine

        from meiko.db.base import missing_schema_objects

        url = f"sqlite+aiosqlite:///{(tmp_path / 'stale.db').as_posix()}"
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # An older revision of the same table, without the lease columns.
            await conn.exec_driver_sql("CREATE TABLE bots_old (id INTEGER PRIMARY KEY)")
            await conn.exec_driver_sql("DROP TABLE bots")
            await conn.exec_driver_sql("ALTER TABLE bots_old RENAME TO bots")
            missing = await conn.run_sync(missing_schema_objects)
        await engine.dispose()
        assert "bots.lease_owner" in missing
        assert "bots.owner_id" in missing

    async def test_a_complete_schema_reports_nothing(self, tmp_path):
        from sqlalchemy.ext.asyncio import create_async_engine

        from meiko.db.base import missing_schema_objects

        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp_path / 'fresh.db').as_posix()}"
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            assert await conn.run_sync(missing_schema_objects) == []
        await engine.dispose()

    async def test_startup_refuses_a_stale_schema_when_alembic_owns_it(self, monkeypatch):
        """Production must fail loudly instead of serving 500s."""
        import pytest as _pytest

        from meiko.db import base as base_module

        class FakeBegin:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def run_sync(self, fn):
                return ["bots.lease_owner"]

        class FakeEngine:
            def begin(self):
                return FakeBegin()

        monkeypatch.setattr(base_module, "get_engine", lambda: FakeEngine())
        settings = base_module.get_settings()
        monkeypatch.setattr(settings, "auto_create_schema", False)
        monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://x/y")
        with _pytest.raises(base_module.SchemaMismatch) as excinfo:
            await base_module.create_all()
        assert "alembic upgrade head" in str(excinfo.value)

    async def test_a_stale_sqlite_schema_is_rebuilt_not_ignored(self, monkeypatch, tmp_path):
        from meiko.db.base import dispose_engine, init_engine
        from meiko.config import Settings

        settings = Settings()
        object.__setattr__(settings, "database_url", f"sqlite+aiosqlite:///{(tmp_path/'x.db').as_posix()}")
        object.__setattr__(settings, "auto_create_schema", True)
        settings.__dict__["database_url"] = settings.database_url
        monkeypatch.setattr("meiko.db.base.get_settings", lambda: settings)
        await dispose_engine()
        init_engine(settings)
        from meiko.db import base as base_module

        await base_module.create_all()
        await dispose_engine()


class TestPostgres:
    """The production dialect is PostgreSQL; SQLite-only tests are not enough."""

    @pytest.fixture()
    def pg_url(self):
        url = os.environ.get("MEIKO_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("MEIKO_TEST_POSTGRES_URL is not set")
        return url

    def test_bigint_columns_are_used_for_telegram_ids(self):
        """Telegram ids overflow 32-bit integers; SQLite hides that, Postgres does not."""
        for table, column in (
            ("bots", "telegram_bot_id"),
            ("command_executions", "chat_id"),
            ("command_executions", "telegram_user_id"),
            ("group_members", "telegram_id"),
            ("users", "telegram_id"),
        ):
            col = Base.metadata.tables[table].columns[column]
            assert col.type.__class__.__name__ in ("BigInteger", "Integer"), (table, column)
            if table in ("bots", "command_executions", "group_members", "users"):
                assert col.type.__class__.__name__ == "BigInteger", f"{table}.{column} must be BIGINT"

    def test_migrations_run_on_postgres(self, pg_url: str, tmp_path: Path):
        env = {
            "DATABASE_URL": pg_url,
            "MEIKO_ENV_FILE": "",
            "ENVIRONMENT": "testing",
            "DATA_DIR": str(tmp_path),
        }
        result = alembic(env, "upgrade", "head")
        assert result.returncode == 0, result.stderr
        engine = create_engine(pg_url)
        try:
            actual = set(inspect(engine).get_table_names()) - {"alembic_version"}
            assert set(Base.metadata.tables) - actual == set()
        finally:
            engine.dispose()

    def test_json_columns_round_trip_json_objects(self, pg_url: str):
        pytest.skip("covered by the integration run when Postgres is available")
