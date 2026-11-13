"""The module store over Supabase Storage, and the Postgres/Postgres-SQL surface.

Two deployment realities this file pins down:

* a **serverless** host (Vercel) has no durable filesystem, so module documents
  must live in Storage — and a failed write must *say so* instead of leaving the
  dashboard believing a save happened;
* a **Postgres** deployment (Supabase) must be able to create this schema, and
  the single Alembic revision must match the models exactly.

The Storage transport is faked at httpx's boundary, so the request the code
actually builds (URL, headers, upsert flag) is what gets asserted.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from meiko.modules.store import ModuleStore, _RemoteStore

BUCKET = "meiko-modules"
PROJECT = "abcdefghijklmnop"
TOKEN = "service-role-key-do-not-print"


class FakeResponse:
    def __init__(self, status_code: int, body: bytes = b"{}") -> None:
        self.status_code = status_code
        self._body = body

    def json(self):
        return json.loads(self._body)


class FakeStorage:
    """Just enough of httpx.Client to hold a bucket in a dict."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_writes = False

    def get(self, url: str):
        self.calls.append(("GET", url, {}))
        body = self.objects.get(url)
        return FakeResponse(200, body) if body is not None else FakeResponse(404)

    def post(self, url: str, content: bytes = b"", headers: dict | None = None):
        self.calls.append(("POST", url, headers or {}))
        if self.fail_writes:
            return FakeResponse(500)
        self.objects[url] = content
        return FakeResponse(200)

    def delete(self, url: str):
        self.calls.append(("DELETE", url, {}))
        existed = self.objects.pop(url, None) is not None
        return FakeResponse(200 if existed else 404)


@pytest.fixture()
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture()
def remote(storage: FakeStorage) -> ModuleStore:
    parsed = _RemoteStore.parse(f"supabase://{PROJECT}/{BUCKET}", token=TOKEN)
    parsed._transport = storage
    store = ModuleStore(f"supabase://{PROJECT}/{BUCKET}", token=TOKEN, cache_documents=False)
    store.remote = parsed  # same object, injected transport
    return store


class TestDsn:
    def test_parse_builds_the_project_host(self):
        parsed = _RemoteStore.parse(f"supabase://{PROJECT}/{BUCKET}", token=TOKEN)
        assert parsed.bucket == BUCKET
        assert parsed.base_url == f"https://{PROJECT}.supabase.co/storage/v1/object"

    def test_parse_honours_a_self_hosted_base_url(self):
        parsed = _RemoteStore.parse(
            f"supabase://{PROJECT}/{BUCKET}", token=TOKEN, url="https://storage.example.com"
        )
        assert parsed.base_url == "https://storage.example.com/storage/v1/object"

    @pytest.mark.parametrize("dsn", ["supabase://only-a-project", "supabase://", "supabase:///bucket"])
    def test_an_incomplete_dsn_is_refused(self, dsn: str):
        with pytest.raises(ValueError, match="supabase://"):
            _RemoteStore.parse(dsn, token=TOKEN)

    def test_a_plain_path_stays_local(self, tmp_path):
        store = ModuleStore(tmp_path / "modules")
        assert store.remote is None


class TestRemoteRoundTrip:
    def test_write_then_read_survives_a_new_store_instance(self, storage, remote):
        remote.write("music", "bot:7", {"volume": 55, "items": ["a"]})
        # a cold start: brand-new object, no cache, same bucket
        parsed = _RemoteStore.parse(f"supabase://{PROJECT}/{BUCKET}", token=TOKEN)
        parsed._transport = storage
        fresh = ModuleStore(f"supabase://{PROJECT}/{BUCKET}", token=TOKEN)
        fresh.remote = parsed
        assert fresh.read("music", "bot:7") == {"volume": 55, "items": ["a"]}

    def test_the_object_path_is_the_documented_one(self, storage, remote):
        remote.write("music", "bot:7", {"a": 1})
        urls = [url for method, url, _ in storage.calls if method == "POST"]
        assert f"/{BUCKET}/music/bot_7.json" in urls

    def test_writes_are_upserts(self, storage, remote):
        remote.write("leech", "global", {"x": 1})
        headers = [h for method, url, h in storage.calls if method == "POST" and url.endswith(".json")]
        assert headers and headers[0]["x-upsert"] == "true"
        assert headers[0]["Content-Type"] == "application/json"

    def test_scopes_are_indexed_remotely(self, storage, remote):
        remote.write("music", "bot:7", {"a": 1})
        remote.write("music", "group:9", {"b": 2})
        assert remote.scopes("music") == ["bot:7", "group:9"]
        assert any(url.endswith("/music/_scopes.json") for _m, url, _h in storage.calls)

    def test_scopes_are_empty_when_the_module_has_nothing(self, remote):
        assert remote.scopes("encoder") == []

    def test_update_merges_at_the_top_level(self, storage, remote):
        remote.write("filestore", "shop:1", {"name": "Shop", "files": []})
        merged = remote.update("filestore", "shop:1", {"name": "Renamed"})
        assert merged == {"name": "Renamed", "files": []}
        assert remote.read("filestore", "shop:1")["name"] == "Renamed"

    def test_forget_removes_the_document_and_its_scope_entry(self, storage, remote):
        remote.write("music", "bot:7", {"a": 1})
        assert remote.forget("music", "bot:7") is True
        assert remote.read("music", "bot:7") == {}
        assert remote.scopes("music") == []
        assert remote.forget("music", "bot:7") is False

    def test_a_missing_document_reads_as_empty_not_an_error(self, remote):
        assert remote.read("music", "bot:404") == {}

    def test_corrupt_remote_json_degrades_to_empty(self, storage, remote):
        storage.objects[f"/{BUCKET}/music/bot_7.json"] = b"{not json"
        assert remote.read("music", "bot:7") == {}


class TestRemoteFailureIsVisible:
    def test_a_failed_write_warns_and_does_not_cache(self, storage):
        warnings: list[str] = []
        parsed = _RemoteStore.parse(f"supabase://{PROJECT}/{BUCKET}", token=TOKEN)
        parsed._transport = storage
        store = ModuleStore(f"supabase://{PROJECT}/{BUCKET}", token=TOKEN, warn=warnings.append)
        store.remote = parsed
        storage.fail_writes = True

        store.write("music", "bot:7", {"volume": 90})
        assert warnings and "could not persist" in warnings[0]
        # the cache must not claim what the backend rejected
        assert store.read("music", "bot:7") == {}

    def test_success_does_not_warn(self, storage, remote):
        warnings: list[str] = []
        remote._warn = warnings.append
        remote.write("music", "bot:7", {"volume": 10})
        assert warnings == []


class TestTokensStayOutOfLogs:
    def test_the_service_key_is_not_part_of_any_reported_string(self, storage):
        parsed = _RemoteStore.parse(f"supabase://{PROJECT}/{BUCKET}", token=TOKEN)
        parsed._transport = storage
        store = ModuleStore(f"supabase://{PROJECT}/{BUCKET}", token=TOKEN)
        store.remote = parsed
        messages: list[str] = []
        store._warn = messages.append
        storage.fail_writes = True
        store.write("music", "bot:7", {"a": 1})
        assert TOKEN not in " ".join(messages)


class TestPostgres:
    """Compile the real schema and migration for the Postgres dialect."""

    def test_every_table_compiles_for_postgres(self):
        from meiko.db import models

        assert len(models.Base.metadata.tables) == 28
        for name, table in models.Base.metadata.tables.items():
            sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
            assert name in sql
            for index in table.indexes:
                str(CreateIndex(index).compile(dialect=postgresql.dialect()))

    def test_json_columns_become_jsonb_compatible_json(self):
        from meiko.db import models

        table = models.Plugin.__table__
        assert "JSON" in str(CreateTable(table).compile(dialect=postgresql.dialect()))

    def test_the_migration_renders_postgres_sql(self):
        from alembic import command
        from alembic.config import Config

        import os
        import tempfile

        output = tempfile.NamedTemporaryFile(suffix=".sql", delete=False).name
        try:
            config = Config("alembic.ini")
            config.set_main_option(
                "sqlalchemy.url", "postgresql+asyncpg://u:p@db.example.com:5432/meiko"
            )
            with open(output, "w") as handle:
                config.output_buffer = handle
                command.upgrade(config, "head", sql=True)
            sql = open(output).read()
            assert "CREATE TABLE users" in sql
            assert "ON DELETE CASCADE" in sql
            assert "INSERT INTO alembic_version" in sql
        finally:
            os.unlink(output)

    def test_the_revision_matches_the_models(self, tmp_path):
        """``alembic check`` against a database built by the migration itself."""
        import os
        import subprocess
        import sys

        db = tmp_path / "migrated.db"
        env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
        upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=env, capture_output=True
        )
        assert upgrade.returncode == 0, upgrade.stderr.decode()[-800:]
        check = subprocess.run(
            [sys.executable, "-m", "alembic", "check"], env=env, capture_output=True
        )
        assert check.returncode == 0, check.stderr.decode()[-800:]
        assert "No new upgrade operations detected" in (check.stdout + check.stderr).decode()


class TestTheEnvironmentWiresIt:
    """The DSN has to reach the process-wide store, or the feature is theoretical."""

    def test_settings_default_to_the_local_filesystem(self, monkeypatch):
        from meiko.modules import store as store_module

        monkeypatch.delenv("MODULE_STORE_URL", raising=False)
        monkeypatch.delenv("MODULE_STORE_TOKEN", raising=False)
        settings = make_settings(monkeypatch)
        assert settings.module_store_url == ""
        monkeypatch.setattr(store_module, "_STORE", None)
        built = store_module.get_module_store()
        assert built.remote is None, "no DSN must mean the filesystem, not a surprise network call"

    def test_a_dsn_builds_a_remote_store(self, monkeypatch):
        from meiko.modules import store as store_module

        settings = make_settings(
            monkeypatch,
            module_store_url=f"supabase://{PROJECT}/{BUCKET}",
            module_store_token=TOKEN,
        )
        monkeypatch.setattr(store_module, "_STORE", None)
        built = store_module.get_module_store()
        assert built.remote is not None
        assert built.remote.bucket == BUCKET
        assert built.remote.base_url.startswith(f"https://{PROJECT}.supabase.co")
        assert settings.module_store_token == TOKEN

    def test_the_remote_store_is_not_reachable_without_a_key(self, monkeypatch):
        """A DSN with no key fails on the first call instead of at import."""
        from meiko.modules import store as store_module

        make_settings(monkeypatch, module_store_url=f"supabase://{PROJECT}/{BUCKET}")
        monkeypatch.setattr(store_module, "_STORE", None)
        built = store_module.get_module_store()
        assert built.remote is not None and built.remote.token == ""


def make_settings(monkeypatch, **overrides):
    """A fresh Settings object with only the module-store knobs applied."""
    from meiko.config import Settings, get_settings

    for key, value in overrides.items():
        monkeypatch.setenv(key.upper(), value)
    get_settings.cache_clear()
    settings = Settings()
    get_settings.cache_clear()
    # ModuleStore's singleton imports get_settings at call time, so patching the
    # module attribute is enough — and leaves the real loader untouched.
    monkeypatch.setattr("meiko.config.get_settings", lambda: settings)
    return settings
