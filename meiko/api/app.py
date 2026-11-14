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
    modules,
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
def _verify_deployment_settings(settings) -> None:
    """Refuse to start a production deployment that cannot work.

    Both of these are silent in the ordinary course of things and expensive when
    they bite:

    * a generated ``SECRET_KEY`` means every restart logs everybody out and, with
      more than one worker, tokens minted by one process are rejected by another;
    * session cookies marked ``Secure`` are dropped by the browser on plain HTTP, so
      a production deployment without TLS appears to accept logins and then loses
      them.
    """
    if not settings.is_production:
        return
    if not settings.secret_key or len(settings.secret_key) < 32:
        raise RuntimeError(
            "SECRET_KEY must be set to at least 32 characters in production; "
            "a generated key invalidates every session on restart and is not shared "
            "between workers"
        )
    from meiko.config import database_tls_warning, migration_url_problem

    tls_problem = database_tls_warning(settings)
    if tls_problem:
        raise RuntimeError(tls_problem)
    pooled_problem = migration_url_problem(settings)
    if pooled_problem:
        log.warning(pooled_problem)
    if not settings.cookie_secure:
        log.warning(
            "COOKIE_SECURE is off in production: session cookies are only protected "
            "if TLS terminates in front of this service and forwards Secure requests"
        )


def _bot_liveness() -> dict[str, Any]:
    """Summarise whether the bots this process owns are actually being serviced.

    A host health check that only proves "the web process answers" will happily
    keep a service alive while every bot in it has stopped polling.  That is the
    real complaint behind "keep-alive is bad sometimes": nothing noticed.  This
    counts the runtimes that are alive, stale, or failed, from each application's
    own liveness, and the full detail is at ``/health/bots``.
    """
    from meiko.runtime.manager import get_worker_manager

    manager = get_worker_manager()
    alive: list[int] = []
    stale: list[int] = []
    failed: dict[str, str] = {}
    for bot_id, application in manager.state.applications.items():
        try:
            check = application.liveness()
        except Exception as exc:  # pragma: no cover - defensive
            failed[str(bot_id)] = str(exc)
            continue
        if check["alive"]:
            alive.append(bot_id)
        else:
            stale.append(bot_id)
            failed[str(bot_id)] = "; ".join(check["reasons"]) or "unhealthy"
    return {
        "workers": len(manager.running_ids()),
        "alive": sorted(alive),
        "stale": sorted(stale),
        "errors": failed,
        "serving": bool(alive) or not manager.running_ids(),
    }


async def lifespan(app: FastAPI):
    settings = get_settings()
    _verify_deployment_settings(settings)
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


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defence-in-depth headers for every API response.

    The console is served by Next.js, which sets its own headers; these cover the
    API itself (and any future server-rendered response), so a browser that lands
    on an API URL cannot be framed or sniffed into something else.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if get_settings().is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Rejects cross-site state changes.

    The console sends its session token in an ``Authorization`` header, which a
    browser will not attach to a cross-site request — but two other paths do not go
    through that header: the dev-login form and any future cookie-based session.
    For every unsafe method this middleware therefore requires that, *when* the
    browser tells us where the request came from, that origin is trusted.  A
    request with no ``Origin``/``Referer`` (curl, server-to-server, tests) is
    allowed, because blocking those would break the API for legitimate clients and
    would not stop a browser anyway.

    Trusted means: same host as the request, or one of ``CORS_ORIGINS``.
    """

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if (
            getattr(settings, "csrf_enforce", True)
            and request.method in UNSAFE_METHODS
            and not request.url.path.startswith("/health")
        ):
            origin = request.headers.get("origin") or ""
            if not origin:
                referer = request.headers.get("referer") or ""
                if referer:
                    from urllib.parse import urlparse

                    parsed = urlparse(referer)
                    origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin:
                host = request.headers.get("host", "")
                same_host = origin.split("://", 1)[-1] == host
                allowed = {item.rstrip("/") for item in settings.cors_origin_list}
                if not same_host and origin.rstrip("/") not in allowed:
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": "csrf",
                            "message": f"cross-site request refused (origin {origin!r})",
                        },
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
    app.add_middleware(OriginGuardMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
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
        modules,
        owner,
        templates,
    ):
        app.include_router(module.router, prefix=prefix)

    # ------------------------------------------------------------- health
    @app.get("/health", tags=["health"])
    async def health(strict: bool = False) -> JSONResponse:
        """Health for humans and for host probes.

        ``?strict=1`` returns 503 whenever a dependency is down, which is what a load
        balancer or container health check should use.  The plain response stays 200
        with ``"status": "degraded"`` so an operator can still read the payload from a
        service the platform considers unhealthy — but that also means a probe pointed
        at ``/health`` alone passes for a completely broken API, which is why
        ``docs/HOSTING.md`` tells you to point it at ``/ready`` or ``/health?strict=1``.
        """
        db = await database_health()
        from meiko.cache import get_cache

        cache = await get_cache().ping()
        status = "ok" if db["status"] == "ok" and cache["status"] == "ok" else "degraded"
        payload: dict[str, Any] = {
            "status": status,
            "bots": _bot_liveness(),
            "service": "api",
            "version": __version__,
            "bot_api": BOT_API_VERSION,
            "uptime_seconds": int(time.time() - STARTED_AT),
            "database": db,
            "cache": cache,
            "maintenance": is_maintenance(),
        }
        return JSONResponse(
            status_code=200 if (status == "ok" or not strict) else 503, content=payload
        )

    @app.get("/ready", tags=["health"])
    async def ready() -> JSONResponse:
        """Readiness: only ready when dependencies really answer."""
        db = await database_health()
        payload = {"ready": db["status"] == "ok", "database": db["status"]}
        return JSONResponse(status_code=200 if payload["ready"] else 503, content=payload)

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/bots", tags=["health"])
    async def health_bots() -> JSONResponse:
        """Per-bot liveness, for a host probe with a stricter check than /health/live.

        Returns 503 when a bot that should be running is not being serviced, so a
        platform that restarts on unhealthy responses recovers by itself instead of
        serving a dead bot silently.
        """
        from meiko.runtime.manager import get_worker_manager

        manager = get_worker_manager()
        bots = {}
        for bot_id, application in manager.state.applications.items():
            bots[str(bot_id)] = application.liveness()
        ok = all(entry["alive"] for entry in bots.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={"healthy": ok, "count": len(bots), "bots": bots},
        )

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
