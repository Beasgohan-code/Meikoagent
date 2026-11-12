"""Module APIs: Music, Encoder, FileStore, Linkshare, Leech and the platform view.

Every endpoint here reads or writes something real:

* music reads the bot's own mirrored queue and posts transport actions to the
  running bot (it answers ``applied: false`` when nothing is running),
* encoder reads the job ledger the encoder writes while ffmpeg runs,
* filestore/linkshare/leech read their durable module stores,
* platform aggregates real ledger/store/event state, not placeholders.

Tenant isolation is enforced by ``_owned_bot_ids``: a bot that is not yours is
treated as missing, and every store scope is derived from a bot id you own.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from meiko.api.deps import CurrentAuth, SessionDep, error
from meiko.modules.events import get_event_hub
from meiko.modules.filestore import FileStoreService, ShopError
from meiko.modules.ledger import JobLedger, StorageLedger
from meiko.modules.leech import TransferEngine, TransferError
from meiko.modules.linkshare import LinkError, LinkshareService
from meiko.modules.mtproto import capabilities as mtproto_capabilities
from meiko.modules.music import MusicSessions, MusicTransport
from meiko.modules.store import get_module_store
from meiko.services.core import BotService

router = APIRouter(tags=["modules"])


# ----------------------------------------------------------------- helpers
async def _owned_bot_ids(session: Any, auth: Any) -> set[int]:
    bots = await BotService(session).list_for_owner(auth.user.id)
    return {bot.id for bot in bots}


async def _require_bot(session: Any, auth: Any, bot_id: int) -> None:
    if bot_id not in await _owned_bot_ids(session, auth):
        raise error(404, "not_found", "bot not found")


def _sessions() -> MusicSessions:
    return MusicSessions(get_module_store())


def _linkshare() -> LinkshareService:
    from meiko.config import get_settings

    return LinkshareService(get_module_store(), get_settings().encryption_key or "meiko-linkshare")


def _filestore() -> FileStoreService:
    return FileStoreService(get_module_store())


def _ledger(module: str, *, limit: int = 10) -> JobLedger:
    return JobLedger(get_module_store(), module, limit=limit)


def _storage() -> StorageLedger:
    return StorageLedger(get_module_store())


def _scope(bot_id: int) -> str:
    return f"bot:{bot_id}"


# ------------------------------------------------------------------- music
class TransportRequest(BaseModel):
    chat_id: int
    action: str
    value: Any = None


@router.get("/music/sessions")
async def music_sessions(auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    store = _sessions()
    return {
        "rooms": store.active_rooms(bot_id),
        "analytics": store.analytics(bot_id),
        "transport_ready": True,
    }


@router.get("/music/room")
async def music_room(auth: CurrentAuth, session: SessionDep, bot_id: int, chat_id: int) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    store = _sessions()
    return {
        "room": store.room(bot_id, chat_id),
        "listeners": store.listeners(bot_id, chat_id),
    }


@router.post("/music/transport")
async def music_transport(
    payload: TransportRequest, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    result = await MusicTransport(_sessions()).dispatch(
        bot_id, payload.chat_id, payload.action, value=payload.value, actor_id=auth.user.id
    )
    if result.get("applied"):
        # refresh the snapshot for the dashboard immediately (no polling lag)
        result["room"] = _sessions().room(bot_id, payload.chat_id)
    return result


@router.get("/music/playlists")
async def music_playlists(auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    return {"playlists": _sessions().playlists(bot_id, owner_id=auth.user.id)}


class PlaylistRequest(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    tracks: list[dict[str, Any]] = Field(default_factory=list)
    chat_id: int = 0


@router.post("/music/playlists")
async def music_save_playlist(
    payload: PlaylistRequest, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    entry = _sessions().save_playlist(
        bot_id, payload.name, payload.tracks, owner_id=auth.user.id, chat_id=payload.chat_id
    )
    return {"saved": True, "playlist": entry}


@router.delete("/music/playlists/{name}")
async def music_delete_playlist(
    name: str, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    try:
        removed = _sessions().delete_playlist(bot_id, name, owner_id=auth.user.id)
    except PermissionError as exc:
        raise error(403, "forbidden", str(exc)) from exc
    if not removed:
        raise error(404, "not_found", "playlist not found")
    return {"deleted": True}


@router.get("/music/analytics")
async def music_analytics(auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    return _sessions().analytics(bot_id)


# --------------------------------------------------------------- websocket
@router.websocket("/music/live")
async def music_live(websocket: WebSocket) -> None:
    """Real-time player sync (WebSocket), authenticated by session token.

    The browser cannot set headers on ``WebSocket``, so the token arrives as a
    query parameter and is verified exactly like a bearer token.  Only events for
    bots the caller owns are forwarded.
    """
    from meiko.db.base import get_sessionmaker
    from meiko.security.crypto import verify_session

    token = websocket.query_params.get("token", "")
    payload = verify_session(token)
    if payload is None:
        await websocket.close(code=4401)
        return
    user_id = int(payload.get("user_id") or payload.get("sub") or 0)
    async with get_sessionmaker()() as session:
        auth = type("A", (), {"user": type("U", (), {"id": user_id})()})()
        owned = await _owned_bot_ids(session, auth)
    if not owned:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    hub = get_event_hub()
    subscription = hub.subscribe({"music"})
    try:
        while True:
            event = await subscription.queue.get()
            scope = event.scope
            bot_id = int(scope.split(":", 1)[1]) if scope.startswith("bot:") else 0
            if bot_id and bot_id not in owned:
                continue
            await websocket.send_text(json.dumps(event.to_dict()))
    except WebSocketDisconnect:  # pragma: no cover - client closed
        pass
    finally:
        hub.unsubscribe(subscription)


# ----------------------------------------------------------------- encoder
@router.get("/encoder/jobs")
async def encoder_jobs(auth: CurrentAuth, session: SessionDep, bot_id: int, limit: int = 30) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    ledger = _ledger("encoder")
    scope = _scope(bot_id)
    return {
        "jobs": [entry.to_dict() for entry in ledger.entries(scope, limit=limit)],
        "active": [entry.to_dict() for entry in ledger.active(scope)],
        "stats": ledger.stats(scope),
        "tools": _encoder_tools(),
    }


def _encoder_tools() -> dict[str, Any]:
    try:
        from meiko.plugins.builtin.encoder.runner import missing_tools, tools_available
    except Exception:  # pragma: no cover - encoder package absent
        return {"available": False, "missing": ["egg"]}
    return {"available": tools_available(), "missing": missing_tools()}


@router.post("/encoder/jobs/{job_id}/cancel")
async def encoder_cancel(
    job_id: str, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    ledger = _ledger("encoder")
    scope = _scope(bot_id)
    entry = next((row for row in ledger.entries(scope, limit=200) if row.id == job_id), None)
    if entry is None:
        raise error(404, "not_found", "job not found")
    if entry.state not in {"queued", "running"}:
        return {"cancelled": False, "reason": f"job is already {entry.state}"}
    # Signal the running bot: it owns the ffmpeg process, the ledger only records
    from meiko.runtime.manager import get_worker_manager

    application = get_worker_manager().state.applications.get(bot_id)
    applied = False
    for plugin in getattr(application, "plugins", None) or []:
        canceller = getattr(plugin, "cancel_remote_job", None)
        if canceller is not None and getattr(plugin, "slug", "") == "encoder":
            applied = bool(await canceller(job_id=job_id, scope=scope))
            break
    ledger.finish(
        scope,
        job_id,
        state="cancelled" if applied else "failed",
        error="" if applied else "the bot is not running; the ffmpeg process was already gone",
        detail="cancelled from the Encoding Studio" if applied else "cancelled from the Encoding Studio",
    )
    return {"cancelled": True, "applied": applied}


class EncodePresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    codec: str = "x265"
    crf: int = Field(default=26, ge=0, le=51)
    preset: str = "medium"
    resolution: str = "original"


@router.get("/encoder/presets")
async def encoder_presets(auth: CurrentAuth) -> dict[str, Any]:
    from meiko.plugins.builtin.encoder.plans import CODECS, CONTAINERS, PRESETS, RESOLUTIONS

    return {
        "codecs": list(CODECS),
        "presets": list(PRESETS),
        "containers": list(CONTAINERS),
        "resolutions": list(RESOLUTIONS),
        "tools": _encoder_tools(),
    }


# --------------------------------------------------------------- filestore
@router.get("/filestore/bots")
async def filestore_bots(auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    owned = sorted(await _owned_bot_ids(session, auth))
    shops = []
    for bot_id in owned:
        shop = _filestore().for_bot(_scope(bot_id), bot_id)
        if shop is not None:
            shops.append(
                {
                    "slug": shop.slug,
                    "bot_id": shop.bot_id,
                    "title": shop.title,
                    "active": shop.active,
                    "usage": shop.usage(),
                }
            )
    return {"shops": shops, "owned_bots": owned}


@router.get("/filestore/{slug}")
async def filestore_detail(slug: str, auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    for bot_id in await _owned_bot_ids(session, auth):
        shop = _filestore().for_bot(_scope(bot_id), bot_id)
        if shop is not None and shop.slug == slug:
            return {"shop": shop.to_dict(include_files=False) | {"usage": shop.usage()}}
    raise error(404, "not_found", "filestore not found")


@router.get("/filestore/{slug}/files")
async def filestore_files(
    slug: str, auth: CurrentAuth, session: SessionDep, query: str = "", page: int = 1
) -> dict[str, Any]:
    service = _filestore()
    for bot_id in await _owned_bot_ids(session, auth):
        shop = service.for_bot(_scope(bot_id), bot_id)
        if shop is None or shop.slug != slug:
            continue
        rows, total = service.list_files(
            _scope(bot_id), slug, user_id=auth.user.id, query=query, page=page
        )
        return {"files": [row.to_dict() for row in rows], "total": total, "page": page}
    raise error(404, "not_found", "filestore not found")


class FileLinkRequest(BaseModel):
    file_id: str
    ttl_days: int = 30
    max_uses: int = 0


@router.post("/filestore/{slug}/links")
async def filestore_link(
    slug: str, payload: FileLinkRequest, auth: CurrentAuth, session: SessionDep
) -> dict[str, Any]:
    from meiko.config import get_settings

    service = _filestore()
    for bot_id in await _owned_bot_ids(session, auth):
        shop = service.for_bot(_scope(bot_id), bot_id)
        if shop is None or shop.slug != slug:
            continue
        if not shop.can_manage(auth.user.id):
            raise error(403, "forbidden", "only the owner or an admin can create links")
        try:
            result = service.issue_link(
                _scope(bot_id),
                slug,
                payload.file_id,
                secret=get_settings().encryption_key or "meiko-linkshare",
                ttl_days=payload.ttl_days,
                max_uses=payload.max_uses,
            )
        except ShopError as exc:
            raise error(400, "invalid", str(exc)) from exc
        bot = await BotService(session).get(bot_id, auth.user.id)
        handle = bot.username or f"bot{bot.bot_id}"
        result["start"] = result["start"].replace("{bot}", handle)
        return {"link": result}
    raise error(404, "not_found", "filestore not found")


# --------------------------------------------------------------- linkshare
class LinkCreateRequest(BaseModel):
    url: str = Field(min_length=3)
    title: str = ""
    ttl_hours: int = 24
    max_claims: int = 0


@router.get("/linkshare/links")
async def linkshare_links(
    auth: CurrentAuth, session: SessionDep, bot_id: int, state: str = "", limit: int = 100
) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    service = _linkshare()
    rows = service.list_links(_scope(bot_id), owner_id=auth.user.id, state=state, limit=limit)
    return {"links": [row.to_dict() for row in rows], "analytics": service.analytics(_scope(bot_id))}


@router.post("/linkshare/links")
async def linkshare_create(
    payload: LinkCreateRequest, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    try:
        link = _linkshare().create(
            _scope(bot_id),
            url=payload.url,
            owner_id=auth.user.id,
            title=payload.title,
            ttl_hours=payload.ttl_hours,
            max_claims=payload.max_claims,
            bot_id=bot_id,
        )
    except LinkError as exc:
        raise error(400, "invalid", str(exc)) from exc
    return {"link": link.to_dict()}


@router.post("/linkshare/links/{code}/revoke")
async def linkshare_revoke(code: str, auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    try:
        revoked = _linkshare().revoke(_scope(bot_id), code, owner_id=auth.user.id)
    except LinkError as exc:
        raise error(403, "forbidden", str(exc)) from exc
    if not revoked:
        raise error(404, "not_found", "link not found")
    return {"revoked": True, "code": code}


@router.get("/linkshare/analytics")
async def linkshare_analytics(auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    return _linkshare().analytics(_scope(bot_id))


# ------------------------------------------------------------------- leech
class TransferRequest(BaseModel):
    source: str = Field(min_length=1)
    destination: str = ""
    kind: str = "download"


@router.get("/leech/sources")
async def leech_sources(auth: CurrentAuth) -> dict[str, Any]:
    engine = TransferEngine()
    return {"engine": engine.describe(), "mtproto": mtproto_capabilities().to_dict()}


@router.post("/leech/validate")
async def leech_validate(payload: TransferRequest, auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    engine = TransferEngine()
    downloader = engine.downloader_for(payload.source)
    if downloader is None:
        return {"valid": False, "reason": "no downloader can handle this source"}
    try:
        info = await downloader.validate(payload.source)
    except TransferError as exc:
        return {"valid": False, "reason": str(exc)}
    return {"valid": True, "downloader": downloader.name, "info": info}


@router.get("/leech/jobs")
async def leech_jobs(auth: CurrentAuth, session: SessionDep, bot_id: int, limit: int = 30) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    ledger = _ledger("leech")
    scope = _scope(bot_id)
    try:
        from meiko.plugins.builtin.leech.tasks import TaskManager
    except Exception:  # pragma: no cover
        TaskManager = None  # type: ignore[assignment]
    return {
        "jobs": [entry.to_dict() for entry in ledger.entries(scope, limit=limit)],
        "stats": ledger.stats(scope),
        "engine": TransferEngine().describe(),
        "task_manager": TaskManager is not None,
    }


# ---------------------------------------------------------------- platform
@router.get("/platform/modules")
async def platform_modules(auth: CurrentAuth) -> dict[str, Any]:
    hub = get_event_hub()
    store = get_module_store()
    modules = {}
    for name in ("music", "encoder", "filestore", "linkshare", "leech", "storage"):
        modules[name] = {
            "scopes": store.scopes(name),
            "events": hub.stats()["topics"].get(name, 0),
        }
    return {
        "modules": modules,
        "events": hub.stats(),
        "storage": _storage().overview("platform"),
        "mtproto": mtproto_capabilities().to_dict(),
        "encoder_tools": _encoder_tools(),
        "server_time": time.time(),
    }


@router.get("/platform/storage")
async def platform_storage(auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    bots = sorted(await _owned_bot_ids(session, auth))
    ledger = _storage()
    per_bot = {}
    for bot_id in bots:
        per_bot[str(bot_id)] = ledger.overview(_scope(bot_id))
    return {"per_bot": per_bot, "platform": ledger.overview("platform"), "bots": bots}


@router.get("/platform/jobs")
async def platform_jobs(auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    bots = sorted(await _owned_bot_ids(session, auth))
    rows: list[dict[str, Any]] = []
    for module in ("encoder", "leech", "saver"):
        ledger = _ledger(module)
        for bot_id in bots:
            for entry in ledger.entries(_scope(bot_id), limit=20):
                rows.append(entry.to_dict())
    rows.sort(key=lambda row: row["created_at"], reverse=True)
    return {"jobs": rows[:50], "count": len(rows)}


__all__ = ["router"]
