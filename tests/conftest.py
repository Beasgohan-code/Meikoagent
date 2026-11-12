"""Test harness: isolated DB, cache and settings per test."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("MEIKO_ENV_FILE", "")


@pytest.fixture(scope="session")
def event_loop():  # pragma: no cover - pytest-asyncio hook
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from meiko.config import Settings, get_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    cfg = get_settings()
    yield cfg
    get_settings.cache_clear()


@pytest_asyncio.fixture()
async def db(settings):
    from meiko.db.base import create_all, dispose_engine, init_engine

    await dispose_engine()
    init_engine(settings)
    await create_all()
    yield
    await dispose_engine()


@pytest_asyncio.fixture()
async def session(db):
    from meiko.db.base import session_scope

    async with session_scope() as s:
        yield s


@pytest.fixture()
def cache():
    from meiko.cache import Cache, MemoryBackend

    return Cache(MemoryBackend(), "test")
