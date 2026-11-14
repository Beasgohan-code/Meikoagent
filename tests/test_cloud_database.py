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
