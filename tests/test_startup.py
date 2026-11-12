"""Startup contract tests: `python start.py` compatibility and schema ownership."""

from __future__ import annotations

from pathlib import Path

import pytest

from start import parse_args


ROOT = Path(__file__).resolve().parent.parent


class TestArgumentContract:
    def test_default_service_is_all(self):
        args = parse_args([])
        assert args.service == "all"
        assert args.bot_id is None
        assert args.reload is False

    def test_every_service_mode_is_accepted(self):
        for service in ("all", "api", "bot", "worker", "scheduler", "watchdog", "migrate", "health"):
            assert parse_args(["--service", service]).service == service

    def test_unknown_service_is_rejected(self, capsys):
        with pytest.raises(SystemExit):
            parse_args(["--service", "nonsense"])

    def test_port_and_host_overrides(self):
        args = parse_args(["--service", "api", "--host", "127.0.0.1", "--port", "9001", "--reload"])
        assert (args.host, args.port, args.reload) == ("127.0.0.1", 9001, True)

    def test_health_flag_alias(self):
        assert parse_args(["--health"]).health is True

    def test_all_in_one_remains_the_documented_entrypoint(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "python start.py" in readme


class TestSchemaOwnership:
    async def test_create_all_is_skipped_when_alembic_owns_schema(self, monkeypatch, tmp_path):
        from meiko.config import get_settings
        from meiko.db import base

        monkeypatch.setenv("AUTO_CREATE_SCHEMA", "false")
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'owned.db').as_posix()}")
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        get_settings.cache_clear()
        try:
            await base.dispose_engine()
            base.init_engine(get_settings())
            await base.create_all()
            health = await base.database_health()
            assert health["status"] == "ok"
            # No tables were created by the app: Alembic owns them.
            import sqlite3

            tables = sqlite3.connect(str(tmp_path / "owned.db")).execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
            assert tables == []
        finally:
            await base.dispose_engine()
            get_settings.cache_clear()

    async def test_create_all_runs_for_local_development(self, monkeypatch, tmp_path):
        from meiko.config import get_settings
        from meiko.db import base

        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'dev.db').as_posix()}")
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.delenv("AUTO_CREATE_SCHEMA", raising=False)
        get_settings.cache_clear()
        try:
            await base.dispose_engine()
            base.init_engine(get_settings())
            await base.create_all()
            import sqlite3

            tables = sqlite3.connect(str(tmp_path / "dev.db")).execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
            assert len(tables) > 25
        finally:
            await base.dispose_engine()
            get_settings.cache_clear()


class TestComposeContract:
    """The compose file must describe the same services start.py exposes."""

    def test_compose_services_cover_platform_roles(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        for service in ("postgres", "redis", "migrate", "api", "bot", "scheduler", "watchdog", "webapp"):
            assert f"\n  {service}:" in compose, service

    def test_compose_disables_app_side_schema_creation(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert 'AUTO_CREATE_SCHEMA: "false"' in compose

    def test_webapp_proxies_to_api_service(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert "MEIKO_API_URL: http://api:8000" in compose
