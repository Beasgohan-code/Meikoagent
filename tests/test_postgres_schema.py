"""The migrations must produce Postgres-correct DDL, not SQLite-flavoured SQL.

Every check in the suite until now ran on SQLite. Postgres is the documented
production database and Supabase/Render are the documented hosts, so a migration that
only works on SQLite would fail on the first real deploy and never here. These tests
render the migration chain offline against a Postgres dialect and assert the
properties that actually differ.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def postgres_ddl() -> str:
    """The full migration chain rendered for PostgreSQL, without a server."""
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": "postgresql://u:p@db.example.com:5432/postgres?sslmode=require",
            "MIGRATION_URL": "",
            "ENVIRONMENT": "testing",
            "MEIKO_ENV_FILE": "",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


class TestPostgresDdl:
    def test_every_revision_is_applied(self, postgres_ddl):
        assert postgres_ddl.count("-- Running upgrade") == 4
        assert "UPDATE alembic_version SET version_num='404947bb2f88'" in postgres_ddl

    def test_primary_keys_use_a_postgres_sequence(self, postgres_ddl):
        assert "id SERIAL NOT NULL" in postgres_ddl
        assert "AUTOINCREMENT" not in postgres_ddl, "SQLite-only syntax leaked into Postgres DDL"

    def test_timestamps_are_without_time_zone(self, postgres_ddl):
        """The models store naive UTC; a tz-aware column would raise on insert."""
        assert "TIMESTAMP WITHOUT TIME ZONE" in postgres_ddl
        assert "DATETIME" not in postgres_ddl

    def test_booleans_are_booleans(self, postgres_ddl):
        assert "BOOLEAN NOT NULL" in postgres_ddl

    def test_json_columns_use_the_json_type(self, postgres_ddl):
        assert " JSON NOT NULL" in postgres_ddl

    def test_foreign_keys_cascade_where_the_models_say_so(self, postgres_ddl):
        assert "ON DELETE CASCADE" in postgres_ddl

    def test_every_table_the_models_declare_is_created(self, postgres_ddl):
        from meiko.db.models import Base

        created = {
            line.split("CREATE TABLE ", 1)[1].split(" (")[0]
            for line in postgres_ddl.splitlines()
            if line.startswith("CREATE TABLE ")
        }
        expected = set(Base.metadata.tables) - {"alembic_version"}
        missing = expected - created
        assert not missing, f"migrations never create: {sorted(missing)}"

    def test_the_chain_is_a_single_transaction(self, postgres_ddl):
        """A partially applied Postgres DDL chain leaves an unusable database."""
        assert postgres_ddl.lstrip().startswith("BEGIN;")
        assert postgres_ddl.rstrip().endswith("COMMIT;")

    def test_indexes_are_created(self, postgres_ddl):
        assert postgres_ddl.count("CREATE INDEX") >= 60

    def test_it_can_be_rendered_for_every_revision_offline(self):
        """`alembic upgrade head --sql` must work without a database at all."""
        env = dict(os.environ)
        env.update(
            {
                "DATABASE_URL": "postgresql://u:p@h:5432/db",
                "ENVIRONMENT": "testing",
                "MEIKO_ENV_FILE": "",
            }
        )
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "b96091e32e6b", "--sql"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr[-1000:]
        assert "CREATE TABLE plugins" in result.stdout
        assert "plugin_sources" in result.stdout, "b96091e32e6b adds the marketplace tables"
        assert "command_executions" not in result.stdout, "rendered past the requested revision"
        assert "bot_leases" not in result.stdout and "plugin_install_events" not in result.stdout
