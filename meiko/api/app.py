"""FastAPI application: control plane, webhooks, health and lifecycle."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from meiko import __version__
from meiko.api.routers import (
    activity,
    auth_routes,
    automation,
    bots,
    commands,
    developer,
    groups,
    infrastructure,
    owner,
    plugins,
    templates,
)
from meiko.api.state import is_maintenance
from meiko.cache import init_cache
from meiko.config import BOT_API_VERSION, get_settings
from meiko.db.base import create_all, database_health, dispose_engine, init_engine, session_scope
from meiko.logging_setup import get_logger, setup_logging
from meiko.services.seed import seed_platform

log = get_logger("api")

STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging("api")
    init_engine(settings)
    await create_all()
    await init_cache()
    async with session_scope() as session:
        await seed_platform(session)
    from meiko.runtime.manager import get_worker_manager

    manager = get_worker_manager()
    await manager.register()
    manager.state.heartbeat_task = asyncio.create_task(manager.heartbeat_loop())
    log.info("MEIKO API ready on %s:%s", settings.api_host, settings.api_port)
    try:
        yield
    finally:
        manager.state.heartbeat_task and manager.state.heartbeat_task.cancel()
        await manager.stop_all()
        await dispose_engine()
        log.info("MEIKO API stopped")


class MaintenanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if is_maintenance() and not request.url.path.startswith("/health"):
            return JSONResponse(
                status_code=503,
                content={"error": "maintenance", "message": "platform is in maintenance mode"},
            )
        return await call_next(request)


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - started) * 1000
        response.headers["X-Response-Time"] = f"{elapsed:.0f}ms"
        if elapsed > 2000:  # pragma: no cover
            log.warning("slow request %s %s took %.0fms", request.method, request.url.path, elapsed)
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="MEIKO AGENT API",
        version=__version__,
        description=(
            "Control plane for the MEIKO AGENT Telegram ecosystem: bots, plugins, "
            "moderation, automation, analytics and infrastructure."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(TimingMiddleware)
    app.add_middleware(MaintenanceMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Dev-User-Id"],
        expose_headers=["X-Response-Time"],
    )

    prefix = settings.api_prefix
    for module in (
        auth_routes,
        bots,
        commands,
        automation,
        plugins,
        groups,
        activity,
        developer,
        infrastructure,
        owner,
        templates,
    ):
        app.include_router(module.router, prefix=prefix)

    # ------------------------------------------------------------- health
    @app.get("/health", tags=["health"])
    async def health() -> dict[str, Any]:
        db = await database_health()
        from meiko.cache import get_cache

        cache = await get_cache().ping()
        status = "ok" if db["status"] == "ok" and cache["status"] == "ok" else "degraded"
        return {
            "status": status,
            "service": "api",
            "version": __version__,
            "bot_api": BOT_API_VERSION,
            "uptime_seconds": int(time.time() - STARTED_AT),
            "database": db,
            "cache": cache,
            "maintenance": is_maintenance(),
        }

    @app.get("/ready", tags=["health"])
    async def ready() -> JSONResponse:
        """Readiness: only ready when dependencies really answer."""
        db = await database_health()
        payload = {"ready": db["status"] == "ok", "database": db["status"]}
        return JSONResponse(status_code=200 if payload["ready"] else 503, content=payload)

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    # -------------------------------------------------------------- bot api
    @app.post(f"{prefix}/telegram/webhook/{{bot_id}}", tags=["webhooks"])
    async def telegram_webhook(bot_id: int, request: Request) -> dict[str, Any]:
        """Receive Telegram updates in webhook mode.

        The secret header is compared against the configured value; the runtime
        for this bot must exist, otherwise updates are rejected (never silently
        dropped into a fake success).
        """
        from meiko.db.models import BotAccount
        from meiko.db.base import session_scope
        from meiko.runtime.manager import get_worker_manager

        settings = get_settings()
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if settings.webhook_secret and secret_header != settings.webhook_secret:
            return JSONResponse(status_code=403, content={"ok": False, "error": "bad secret"})
        manager = get_worker_manager()
        application = manager.state.applications.get(bot_id)
        if application is None:
            async with session_scope() as session:
                bot = await session.get(BotAccount, bot_id)
                if bot is None:
                    return JSONResponse(status_code=404, content={"ok": False, "error": "unknown bot"})
            try:
                await manager.start(bot_id)
                application = manager.state.applications.get(bot_id)
            except Exception as exc:  # pragma: no cover
                log.warning("could not start bot %s for webhook: %s", bot_id, exc)
        if application is None:
            return JSONResponse(status_code=503, content={"ok": False, "error": "runtime unavailable"})
        payload = await request.json()
        await application.feed_update(payload)
        return {"ok": True}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "an unexpected error occurred"},
        )

    return app


app = create_app()
