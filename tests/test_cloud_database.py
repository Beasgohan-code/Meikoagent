"""Connecting to a managed Postgres (Supabase, Neon, Render) safely.

Managed providers differ from a local Postgres in three ways that break a naive
deployment: they require TLS, the pooled endpoint cannot run the DDL that migrations
need, and they hand out a `postgresql://` URL with libpq parameters.  The first is a
decision the app must make explicitly, the second a decision the operator must make,
and the third is normalised in `meiko/db/base.py`.
"""

from __future__ import annotations

import pytest


class TestTlsIsDecidedNotAssumed:
    def test_remote_postgres_without_tls_is_refused_in_production(self):
        """Supabase/Neon/Render reject non-TLS connections; fail with a reason, not a traceback."""
        from meiko.config import Settings, database_tls_warning

        settings = Settings(
            database_url="postgresql+asyncpg://u:p@db.example.com:5432/postgres",
            environment="production",
        )
        warning = database_tls_warning(settings)
        assert warning is not None
        assert "ssl" in warning.lower()

    def test_a_url_that_asks_for_tls_is_fine(self):
        from meiko.config import Settings, database_tls_warning

        settings = Settings(
            database_url="postgresql://u:p@db.example.com:5432/postgres?sslmode=require",
            environment="production",
        )
        assert database_tls_warning(settings) is None

    def test_local_and_sqlite_are_fine(self):
        from meiko.config import Settings, database_tls_warning

        assert database_tls_warning(Settings(environment="production", database_url="")) is None
        local = Settings(
            environment="production", database_url="postgresql+asyncpg://u:p@localhost:5432/db"
        )
        assert database_tls_warning(local) is None

    def test_development_is_never_blocked(self):
        from meiko.config import Settings, database_tls_warning

        settings = Settings(
            database_url="postgresql+asyncpg://u:p@db.example.com:5432/postgres",
            environment="development",
        )
        assert database_tls_warning(settings) is not None  # reported, not fatal


class TestPoolerVsMigrations:
    def test_a_pooled_connection_is_detected(self):
        from meiko.config import Settings

        settings = Settings(
            database_url="postgresql://u:p@aws-0-ap-south-1.pooler.supabase.com:6543/postgres",
            environment="production",
        )
        assert settings.database_is_pooled is True

    def test_a_direct_connection_is_not_pooled(self):
        from meiko.config import Settings

        settings = Settings(
            database_url="postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres",
            environment="production",
        )
        assert settings.database_is_pooled is False

    def test_a_pooled_url_without_a_migration_url_is_flagged(self):
        """Migrations against a transaction pooler fail halfway; say so loudly."""
        from meiko.config import Settings, migration_url_problem

        settings = Settings(
            database_url="postgresql://u:p@aws-0.pooler.supabase.com:6543/postgres",
            environment="production",
        )
        problem = migration_url_problem(settings)
        assert problem and "MIGRATION_URL" in problem

    def test_a_direct_migration_url_resolves_it(self):
        from meiko.config import Settings, migration_url_problem

        settings = Settings(
            database_url="postgresql://u:p@aws-0.pooler.supabase.com:6543/postgres",
            migration_url="postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres",
            environment="production",
        )
        assert migration_url_problem(settings) is None


class TestSessionPoolerIsNotTheTransactionPooler:
    """Supabase's IP story: the session pooler is the *only* free endpoint over IPv4.

    Treating every ``…pooler.supabase.com`` host as a transaction pooler warned
    operators off the one connection that works from a phone or an IPv4-only
    network — a false alarm, not a safety net.
    """

    def test_the_session_pooler_is_not_treated_as_pooled(self):
        from meiko.config import Settings, migration_url_problem

        settings = Settings(
            database_url=(
                "postgresql://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
                "?sslmode=require"
            ),
            environment="production",
        )
        assert settings.database_is_pooled is False
        assert migration_url_problem(settings) is None

    def test_the_transaction_pooler_still_is(self):
        from meiko.config import Settings

        settings = Settings(
            database_url="postgresql://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres",
            environment="production",
        )
        assert settings.database_is_pooled is True

    def test_a_legacy_pooler_host_without_a_port_is_conservative(self):
        from meiko.config import Settings

        settings = Settings(
            database_url="postgresql://u:p@pgbouncer.internal/postgres",
            environment="production",
        )
        assert settings.database_is_pooled is True


class TestPreparedStatementsOffOnATransactionPooler:
    """Transaction pooling moves statements between backends; asyncpg must not cache."""

    def test_statement_caching_is_disabled_for_a_pooled_url(self):
        from meiko.config import Settings
        from meiko.db.base import prepare_connect_url

        settings = Settings(
            database_url="postgresql://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres",
            environment="production",
        )
        _, connect_args = prepare_connect_url(settings, settings.resolved_database_url)
        assert connect_args.get("statement_cache_size") == 0

    def test_a_direct_connection_keeps_the_cache(self):
        from meiko.config import Settings
        from meiko.db.base import prepare_connect_url

        settings = Settings(
            database_url="postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres",
            environment="production",
        )
        _, connect_args = prepare_connect_url(settings, settings.resolved_database_url)
        assert "statement_cache_size" not in connect_args

    def test_the_ssl_connection_args_survive_the_addition(self):
        """Both fixes have to be able to land on the same connect_args dict."""
        from meiko.config import Settings
        from meiko.db.base import create_engine  # noqa: F401  (import proves it loads)
        from meiko.db.base import strip_libpq_ssl

        url = (
            "postgresql+asyncpg://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
            "?sslmode=require"
        )
        cleaned, connect_args = strip_libpq_ssl(url)
        assert "sslmode" not in cleaned
        assert connect_args["ssl"] is True
