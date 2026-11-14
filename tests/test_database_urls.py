"""Provider connection strings must work as pasted.

Render and Supabase both hand out plain ``postgresql://…`` URLs, often with
``?sslmode=require``.  Two things broke on that:

* SQLAlchemy picked the synchronous psycopg driver, which is not installed — the
  deployment died at boot with ``ModuleNotFoundError: No module named 'psycopg'``;
* the asyncpg dialect forwards every URL query parameter to ``asyncpg.connect``, so
  ``sslmode`` raised ``unexpected keyword argument`` even after the driver was right.

Both are now normalised, and these tests fail if either regresses.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest


class TestSchemeNormalisation:
    @pytest.mark.parametrize(
        "raw",
        [
            "postgresql://u:p@h:5432/db",
            "postgres://u:p@h:5432/db",
            "POSTGRESQL://u:p@h:5432/db",
        ],
    )
    def test_a_plain_postgres_url_uses_asyncpg(self, raw):
        from meiko.config import normalize_async_database_url

        assert normalize_async_database_url(raw).startswith("postgresql+asyncpg://")

    def test_an_already_async_url_is_left_alone(self):
        from meiko.config import normalize_async_database_url

        url = "postgresql+asyncpg://u:p@h/db"
        assert normalize_async_database_url(url) == url

    @pytest.mark.parametrize(
        "raw",
        ["sqlite+aiosqlite:///./data/meiko.db", "mysql+aiomysql://u@h/db", ""],
    )
    def test_other_schemes_are_untouched(self, raw):
        from meiko.config import normalize_async_database_url

        assert normalize_async_database_url(raw) == raw

    def test_settings_resolve_through_normalisation(self):
        from meiko.config import Settings

        settings = Settings(
            database_url="postgresql://u:p@h/db?sslmode=require", environment="testing"
        )
        assert settings.resolved_database_url.startswith("postgresql+asyncpg://")

    def test_a_supabase_pooler_url_creates_an_engine(self):
        from meiko.config import Settings
        from meiko.db.base import create_engine

        settings = Settings(
            database_url="postgresql://u:p@aws-0.pooler.supabase.com:6543/postgres?sslmode=require",
            environment="testing",
        )
        engine = create_engine(settings, settings.resolved_database_url)
        assert engine.url.drivername == "postgresql+asyncpg"
        assert engine.url.drivername != "postgresql+psycopg"


class TestLibpqSslTranslated:
    def test_sslmode_is_moved_out_of_the_url(self):
        from meiko.db.base import strip_libpq_ssl

        url, connect_args = strip_libpq_ssl("postgresql+asyncpg://u:p@h:6543/db?sslmode=require")
        assert "sslmode" not in url
        assert connect_args == {"ssl": True}

    def test_useful_query_parameters_survive(self):
        from meiko.db.base import strip_libpq_ssl

        url, connect_args = strip_libpq_ssl(
            "postgresql+asyncpg://u:p@h/db?sslmode=require&application_name=meiko"
        )
        assert "application_name=meiko" in url
        assert "sslmode" not in url
        assert connect_args == {"ssl": True}

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [("require", True), ("verify-full", "verify-full"), ("disable", False), ("prefer", True)],
    )
    def test_sslmode_values_map_onto_asyncpg(self, mode, expected):
        from meiko.db.base import strip_libpq_ssl

        _, connect_args = strip_libpq_ssl(f"postgresql+asyncpg://u@h/db?sslmode={mode}")
        assert connect_args["ssl"] == expected

    def test_no_parameters_means_no_connect_args(self):
        from meiko.db.base import strip_libpq_ssl

        url, connect_args = strip_libpq_ssl("postgresql+asyncpg://u@h/db")
        assert connect_args == {}
        assert url == "postgresql+asyncpg://u@h/db"

    def test_sqlite_is_untouched(self):
        from meiko.db.base import strip_libpq_ssl

        url, connect_args = strip_libpq_ssl("sqlite+aiosqlite:///./data/meiko.db")
        assert connect_args == {}
        assert url == "sqlite+aiosqlite:///./data/meiko.db"


class TestAsyncpgActuallyReceivesIt:
    """The unit above could pass while asyncpg still got `sslmode`."""

    def test_connect_is_called_with_ssl_and_no_sslmode(self):
        from meiko.config import Settings
        from meiko.db.base import create_engine

        captured: dict = {}

        class FakeConnection:
            async def set_type_codec(self, *args, **kwargs):
                return None

            async def set_builtin_type_codec(self, *args, **kwargs):
                return None

            def close(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        async def fake_connect(*args, **kwargs):
            captured.update(kwargs)
            return FakeConnection()

        async def scenario():
            engine = create_engine(
                Settings(environment="testing"),
                "postgresql+asyncpg://u:p@h:6543/db?sslmode=require",
            )
            module = __import__("asyncpg", fromlist=["connect"])
            with patch.object(module, "connect", fake_connect):
                with pytest.raises(Exception):
                    async with engine.connect():
                        pass
            await engine.dispose()

        asyncio.run(scenario())
        assert captured.get("ssl") is True
        assert "sslmode" not in captured


class TestAlembicUsesTheSameRules:
    def test_migrations_do_not_see_a_libpq_url(self):
        """Alembic runs `alembic upgrade head` as its own async env; it must agree."""
        from pathlib import Path

        env = (Path(__file__).resolve().parents[1] / "migrations/env.py").read_text()
        assert "migration_database_url" in env, "alembic stopped using the settings URL"

        # and that property must normalise, or the migrate step fails where the API works
        from meiko.config import Settings

        settings = Settings(
            database_url="sqlite+aiosqlite:///./data/meiko.db",
            migration_url="postgresql://u:p@h:5432/db?sslmode=require",
            environment="testing",
        )
        assert settings.migration_database_url.startswith("postgresql+asyncpg://")


class TestMigrationsDoNotSilenceLogging:
    """`fileConfig()` disables existing loggers — a real bug, found via a flaky test."""

    def test_env_py_does_not_call_fileconfig(self):
        from pathlib import Path

        env = (Path(__file__).resolve().parents[1] / "migrations/env.py").read_text()
        code = "\n".join(
            line for line in env.splitlines() if not line.lstrip().startswith("#")
        )
        assert "fileConfig(" not in code.replace("fileConfig(config.config_file_name)", ""), (
            "migrations/env.py configures logging; that disables every logger already "
            "created in the process (start.py --service migrate, and caplog in tests)"
        )

    def test_a_logger_survives_a_migration(self, tmp_path, monkeypatch):
        """Reproduce the sequence: configure logging, migrate, then try to log."""
        import logging

        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}")
        monkeypatch.setenv("MEIKO_ENV_FILE", "")
        from meiko.config import get_settings

        get_settings.cache_clear()

        from alembic import command
        from alembic.config import Config

        from meiko.logging_setup import get_logger, setup_logging

        setup_logging("migrate-test")
        probe = get_logger("migration.probe")
        assert probe.disabled is False

        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
        )
        # offline mode: parses the environment file without touching a database
        command.upgrade(config, "head", sql=False)
        assert probe.disabled is False, (
            "running migrations disabled an existing logger; every log line after "
            "`start.py --service migrate` was being dropped"
        )
        assert logging.getLogger().handlers, "root handler was removed by alembic"
        get_settings.cache_clear()
