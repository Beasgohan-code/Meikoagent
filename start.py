#!/usr/bin/env python3
"""MEIKO AGENT entry point.

Backwards compatible with ``python start.py`` (runs every service in one process)
and explicit per-service modes for container deployments:

    python start.py                       # all-in-one (API + bot + workers)
    python start.py --service api         # FastAPI control plane only
    python start.py --service bot         # main bot only (polling)
    python start.py --service worker      # clone worker manager only
    python start.py --service scheduler   # scheduler only
    python start.py --service watchdog    # watchdog only
    python start.py --service migrate     # alembic upgrade head
    python start.py --health              # local health report, exit code 0/1
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="start.py", description="MEIKO AGENT launcher")
    parser.add_argument(
        "--service",
        default="all",
        choices=["all", "api", "bot", "worker", "scheduler", "watchdog", "migrate", "health"],
        help="which service to run (default: all)",
    )
    parser.add_argument("--bot-id", type=int, default=None, help="bot id for --service bot")
    parser.add_argument("--host", default=None, help="API bind host")
    parser.add_argument("--port", type=int, default=None, help="API bind port")
    parser.add_argument("--reload", action="store_true", help="uvicorn reload (development)")
    parser.add_argument("--health", action="store_true", help="print health and exit")
    return parser.parse_args(argv)


async def _bootstrap(create_schema: bool = True) -> None:
    """Prepare engine, cache and seed data.

    ``create_schema`` runs ``create_all`` which is handy for SQLite dev runs and
    tests; container deployments pass ``False`` so the schema is owned solely by
    Alembic (``--service migrate``) and the two never race each other.
    """
    from meiko.cache import init_cache
    from meiko.config import get_settings
    from meiko.db.base import create_all, init_engine, session_scope
    from meiko.services.seed import seed_platform

    settings = get_settings()
    init_engine(settings)
    if create_schema:
        await create_all()
    await init_cache()
    async with session_scope() as session:
        await seed_platform(session)


async def run_api(host: str, port: int, reload: bool = False) -> None:
    import uvicorn

    config = uvicorn.Config(
        "meiko.api.app:app",
        host=host,
        port=port,
        reload=reload,
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_main_bot(bot_id: int | None) -> None:
    from meiko.bot import run_bot_forever
    from meiko.config import get_settings
    from meiko.db.base import session_scope
    from meiko.db.models import BotAccount
    from meiko.runtime.manager import get_worker_manager
    from sqlalchemy import select

    settings = get_settings()
    if bot_id is None:
        if settings.bot_token:
            from meiko.db.models import User

            async with session_scope() as session:
                owner = await session.scalar(select(User).where(User.is_owner.is_(True)))
                owner_id = owner.id if owner else 1
                existing = await session.scalar(
                    select(BotAccount).where(BotAccount.token_fingerprint.is_not(None))
                )
                from meiko.services.core import BotService
                from meiko.telegram.client import build_bot, fetch_me

                service = BotService(session)
                bot = await service.get_by_token(settings.bot_token)
                if bot is None:
                    bot = await service.register(owner_id, token=settings.bot_token, name="Meiko Main")
                client = build_bot(settings.bot_token)
                try:
                    me = await fetch_me(client)
                    await service.verify_identity(
                        bot,
                        telegram_bot_id=me["id"],
                        username=me.get("username"),
                        first_name=me.get("first_name"),
                    )
                finally:
                    await client.session.close()
                bot_id = bot.id
        else:
            async with session_scope() as session:
                row = await session.scalar(
                    select(BotAccount).where(BotAccount.token_valid.is_(True)).order_by(BotAccount.id)
                )
                bot_id = row.id if row else None
    if bot_id is None:
        raise SystemExit(
            "No bot token configured. Set BOT_TOKEN or connect a bot through the API first."
        )
    manager = get_worker_manager()
    await manager.register()
    manager.state.heartbeat_task = asyncio.create_task(manager.heartbeat_loop())
    result = await manager.start(bot_id)
    if result.get("status") != "running":
        raise SystemExit(f"bot {bot_id} failed to start: {result.get('error')}")
    stop_event = asyncio.Event()

    def _signal(*_: Any) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal)
    await stop_event.wait()
    await manager.stop_all()


async def run_worker() -> None:
    from meiko.runtime.manager import get_worker_manager

    manager = get_worker_manager()
    await manager.register()
    manager.state.heartbeat_task = asyncio.create_task(manager.heartbeat_loop())
    recovery = await manager.recover()
    print(f"[worker] recovered: {recovery}")
    stop_event = asyncio.Event()

    def _signal(*_: Any) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal)
    await stop_event.wait()
    await manager.stop_all()


async def run_scheduler() -> None:
    from meiko.runtime.scheduler import get_scheduler

    scheduler = get_scheduler()
    scheduler.start()
    print("[scheduler] running")
    stop_event = asyncio.Event()

    def _signal(*_: Any) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal)
    await stop_event.wait()
    await scheduler.stop()


async def run_watchdog() -> None:
    from meiko.runtime.watchdog import get_watchdog

    watchdog = get_watchdog()
    watchdog.start()
    print("[watchdog] monitoring")
    stop_event = asyncio.Event()

    def _signal(*_: Any) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal)
    await stop_event.wait()
    await watchdog.stop()


async def run_all(host: str, port: int, reload: bool) -> None:
    """All-in-one: API + clone worker manager + scheduler + watchdog."""
    from meiko.config import get_settings
    from meiko.runtime.manager import get_worker_manager
    from meiko.runtime.scheduler import get_scheduler
    from meiko.runtime.watchdog import get_watchdog

    settings = get_settings()
    manager = get_worker_manager()
    await manager.register()
    manager.state.heartbeat_task = asyncio.create_task(manager.heartbeat_loop())
    if settings.run_main_bot:
        recovery = await manager.recover()
        if recovery["started"] or recovery["failed"]:
            print(f"[all] recovered bots: {recovery}")
    scheduler = get_scheduler()
    if settings.scheduler_enabled:
        scheduler.start()
    watchdog = get_watchdog()
    if settings.watchdog_enabled:
        watchdog.start()
    print(f"[all] API on http://{host}:{port} · docs at /docs")
    try:
        await run_api(host, port, reload)
    finally:
        await scheduler.stop()
        await watchdog.stop()
        await manager.stop_all()


async def health_check() -> int:
    from meiko.cache import get_cache, init_cache
    from meiko.db.base import database_health, init_engine, dispose_engine

    init_engine()
    await init_cache()
    db = await database_health()
    cache = await get_cache().ping()
    ok = db["status"] == "ok" and cache["status"] == "ok"
    print(f"database: {db['status']}  cache: {cache['status']}")
    await dispose_engine()
    return 0 if ok else 1


def run_migrations() -> int:
    """Run ``alembic upgrade head`` against the configured database."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from meiko.config import get_settings
    from meiko.logging_setup import setup_logging

    settings = get_settings()
    setup_logging(args.service)

    if args.service == "health" or args.health:
        return asyncio.run(health_check())
    if args.service == "migrate":
        # Alembic owns the schema: migrate first (its async env owns the loop),
        # then bootstrap without create_all and seed the catalogue.
        code = run_migrations()
        asyncio.run(_bootstrap(create_schema=False))
        print("[meiko] migrations applied and platform seeded")
        return code

    asyncio.run(_bootstrap())
    host = args.host or settings.api_host
    port = args.port or settings.api_port
    try:
        if args.service == "api":
            asyncio.run(run_api(host, port, args.reload))
        elif args.service == "bot":
            asyncio.run(run_main_bot(args.bot_id))
        elif args.service == "worker":
            asyncio.run(run_worker())
        elif args.service == "scheduler":
            asyncio.run(run_scheduler())
        elif args.service == "watchdog":
            asyncio.run(run_watchdog())
        else:
            asyncio.run(run_all(host, port, args.reload))
    except KeyboardInterrupt:
        print("\n[meiko] shutdown requested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
