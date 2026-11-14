"""Migrations must connect with the string the provider hands you.

`meiko/db/base.py` normalises the *application* engine's URL: the scheme, the
libpq-style `sslmode` parameter, and statement caching on a transaction pooler.
The migration environment used to build its own engine and bypass all of it, so the
first step on a fresh cloud database —

    python start.py --service migrate

— died with ``TypeError: connect() got an unexpected keyword argument 'sslmode'``
before it opened a socket, even though the app itself connected fine. Reproduced
against a live Supabase project:

    alembic upgrade head   # before: TypeError ... 'sslmode'
    alembic upgrade head   # after:  ConnectionResetError (server reached; the
                           #         password in the test URL is a placeholder)

These tests pin the shared helper and the fact that Alembic uses it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "migrations" / "env.py"

SUPABASE_SESSION_POOLER = (
    "postgresql://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    "?sslmode=require"
)
SUPABASE_TRANSACTION_POOLER = (
    "postgresql://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
    "?sslmode=require"
)


class TestTheMigrationEnvironmentSharesTheAppFix:
    def test_env_uses_the_shared_helper(self):
        text = ENV.read_text()
        assert "prepare_connect_url" in text, (
            "migrations/env.py builds its own engine; it must normalise the URL the "
            "same way the app does, or a managed database cannot be migrated"
        )
        assert "settings.migration_database_url" in text

    def test_env_does_not_re_implement_the_ssl_mapping(self):
        """Two copies of the sslmode rule would drift apart again.

        Comments are free to explain the trap; only executable lines are checked.
        """
        code = [
            line for line in ENV.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert not any("strip_libpq_ssl(" in line for line in code)
        assert not any("sslmode" in line for line in code)

    def test_env_never_uses_fileconfig(self):
        """fileConfig(disable_existing_loggers=True) silences the whole process."""
        assert not re.search(r"^\s*fileConfig\(", ENV.read_text(), re.MULTILINE)


class TestPrepareConnectUrl:
    @pytest.fixture(scope="class")
    def prepare(self):
        from meiko.db.base import prepare_connect_url

        return prepare_connect_url

    def test_sslmode_never_reaches_asyncpg(self, prepare):
        from meiko.config import Settings

        settings = Settings(database_url=SUPABASE_SESSION_POOLER, environment="production")
        url, connect_args = prepare(settings, settings.resolved_database_url)
        assert "sslmode" not in url
        assert connect_args["ssl"] is True

    def test_the_scheme_is_the_async_driver(self, prepare):
        from meiko.config import Settings

        settings = Settings(database_url=SUPABASE_SESSION_POOLER, environment="production")
        url, _ = prepare(settings, settings.resolved_database_url)
        assert url.startswith("postgresql+asyncpg://")

    def test_the_session_pooler_keeps_statement_caching(self, prepare):
        """Port 5432 holds a backend connection, so caching is safe — and faster."""
        from meiko.config import Settings

        settings = Settings(database_url=SUPABASE_SESSION_POOLER, environment="production")
        _, connect_args = prepare(settings, settings.resolved_database_url)
        assert "statement_cache_size" not in connect_args

    def test_the_transaction_pooler_disables_it(self, prepare):
        from meiko.config import Settings

        settings = Settings(database_url=SUPABASE_TRANSACTION_POOLER, environment="production")
        _, connect_args = prepare(settings, settings.resolved_database_url)
        assert connect_args["statement_cache_size"] == 0
        assert connect_args["ssl"] is True

    def test_sqlite_is_untouched(self, prepare):
        from meiko.config import Settings

        settings = Settings(environment="development")
        url, connect_args = prepare(settings, settings.resolved_database_url)
        assert url.startswith("sqlite")
        assert connect_args == {}

    def test_a_local_postgres_gains_no_arguments_it_did_not_ask_for(self, prepare):
        from meiko.config import Settings

        settings = Settings(
            database_url="postgresql://u:p@localhost:5432/meiko", environment="production"
        )
        _, connect_args = prepare(settings, settings.resolved_database_url)
        assert connect_args == {}
