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

import inspect
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
    from meiko.runtime.manager import get_worker_manager

    application = get_worker_manager().state.applications.get(bot_id)
    live: list[dict[str, Any]] = []
    for plugin in getattr(application, "plugins", None) or []:
        if getattr(plugin, "slug", "") == "leech" and hasattr(plugin, "live_tasks"):
            live = plugin.live_tasks()
            break
    return {
        "bot_running": application is not None,
        "jobs": [entry.to_dict() for entry in ledger.entries(scope, limit=limit)],
        "stats": ledger.stats(scope),
        "live": live,
        "engine": TransferEngine().describe(),
    }


# ------------------------------------------------------------------- saver
SAVER_SLUG = "saver"


async def _saver_state(session: Any, auth: Any, bot_id: int) -> dict[str, Any]:
    """The saver's own durable state, as the bot writes it (no mirror, no guesses)."""
    from meiko.plugins.support.state import read_mirror

    scope = _scope(bot_id)
    return {
        "config": read_mirror(SAVER_SLUG, scope, "saver:config", {}) or {},
        "index": read_mirror(SAVER_SLUG, scope, "index:saved", []) or [],
    }


@router.get("/saver/overview")
async def saver_overview(auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    state = await _saver_state(session, auth, bot_id)
    entries = state["index"] if isinstance(state["index"], list) else []
    total = sum(int(entry.get("size") or 0) for entry in entries if isinstance(entry, dict))
    from meiko.modules.ledger import human_bytes

    return {
        "config": state["config"],
        "library": {
            "count": len(entries),
            "bytes": total,
            "human": human_bytes(total),
        },
        "jobs": [entry.to_dict() for entry in _ledger(SAVER_SLUG).entries(_scope(bot_id), limit=20)],
        "policy": {
            "authorized_sources_only": True,
            "note": (
                "Meiko stores and re-delivers content the bot can already access with its own "
                "permissions. It never bypasses protections and never uses a user session to "
                "reach restricted chats."
            ),
        },
    }


class SaverConfigRequest(BaseModel):
    caption_template: str | None = None
    use_caption: bool | None = None
    send_mode: str | None = None
    max_batch: int | None = None
    skip_duplicates: bool | None = None
    notify_each: bool | None = None
    delay_seconds: float | None = None
    delete_words: list[str] | None = None
    replace_words: list[str] | None = None


@router.patch("/saver/settings")
async def saver_settings(
    payload: SaverConfigRequest, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    """Write saver settings the bot will pick up on its next reload."""
    await _require_bot(session, auth, bot_id)
    from meiko.plugins.builtin.saver.state import SaverConfig
    from meiko.plugins.support.state import read_mirror, write_mirror

    scope = _scope(bot_id)
    current = SaverConfig.from_dict(read_mirror(SAVER_SLUG, scope, "saver:config", {}))
    patch = {key: value for key, value in payload.model_dump().items() if value is not None}
    updated = SaverConfig.from_dict({**current.to_dict(), **patch})
    import time as _time

    updated.updated_at = _time.strftime("%Y-%m-%dT%H:%M:%S+00:00", _time.gmtime())
    write_mirror(SAVER_SLUG, scope, "saver:config", updated.to_dict())
    await _reload_plugin(bot_id, SAVER_SLUG)
    return {"saved": True, "config": updated.to_dict()}


@router.get("/saver/history")
async def saver_history(auth: CurrentAuth, session: SessionDep, bot_id: int, limit: int = 50) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    state = await _saver_state(session, auth, bot_id)
    entries = [entry for entry in state["index"] if isinstance(entry, dict)]
    return {"saved": entries[:limit], "total": len(entries)}


@router.delete("/saver/history/{message_id}")
async def saver_forget(
    message_id: int, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    """Remove one record from the saved index (the Telegram post is untouched)."""
    await _require_bot(session, auth, bot_id)
    from meiko.plugins.support.state import read_mirror, write_mirror

    scope = _scope(bot_id)
    entries = read_mirror(SAVER_SLUG, scope, "index:saved", []) or []
    kept = [entry for entry in entries if int(entry.get("message_id") or 0) != message_id]
    if len(kept) == len(entries):
        raise error(404, "not_found", "no saved record for that message")
    write_mirror(SAVER_SLUG, scope, "index:saved", kept)
    await _reload_plugin(bot_id, SAVER_SLUG)
    return {"removed": True, "remaining": len(kept)}


@router.post("/saver/retention")
async def saver_retention(
    keep_days: int, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    """Drop index records older than N days (retention for the library view)."""
    await _require_bot(session, auth, bot_id)
    from datetime import datetime, timedelta, timezone

    from meiko.plugins.support.state import read_mirror, write_mirror

    scope = _scope(bot_id)
    entries = read_mirror(SAVER_SLUG, scope, "index:saved", []) or []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, keep_days))
    kept = []
    for entry in entries:
        raw = str(entry.get("saved_at") or "")
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            kept.append(entry)
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp >= cutoff:
            kept.append(entry)
    write_mirror(SAVER_SLUG, scope, "index:saved", kept)
    await _reload_plugin(bot_id, SAVER_SLUG)
    return {"kept": len(kept), "removed": len(entries) - len(kept), "keep_days": keep_days}


async def _reload_plugin(bot_id: int, slug: str) -> dict[str, Any]:
    """Ask a running bot to re-read its plugin state; report honestly otherwise."""
    from meiko.runtime.manager import get_worker_manager

    application = get_worker_manager().state.applications.get(bot_id)
    if application is None:
        return {"applied": False, "reason": "bot is not running; it will load this on next start"}
    for plugin in getattr(application, "plugins", None) or []:
        if getattr(plugin, "slug", "") != slug:
            continue
        reloader = getattr(plugin, "reload_settings", None)
        if reloader is None:
            return {"applied": False, "reason": "this build cannot reload settings live"}
        outcome = reloader()
        if inspect.isawaitable(outcome):
            await outcome
        return {"applied": True}
    return {"applied": False, "reason": f"{slug} is not loaded for this bot"}


# ------------------------------------------------- saver import / export
@router.get("/saver/export")
async def saver_export(auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    """Portable export of the saver's own settings and index (no credentials)."""
    await _require_bot(session, auth, bot_id)
    state = await _saver_state(session, auth, bot_id)
    return {
        "format": "meiko.saver.v1",
        "exported_at": int(time.time()),
        "bot_id": bot_id,
        "config": state["config"],
        "index": state["index"],
    }


class SaverImportRequest(BaseModel):
    format: str = "meiko.saver.v1"
    config: dict[str, Any] = Field(default_factory=dict)
    index: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/saver/import")
async def saver_import(
    payload: SaverImportRequest, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    """Restore an export into this bot (validated, merged, never overwriting)."""
    await _require_bot(session, auth, bot_id)
    if payload.format != "meiko.saver.v1":
        raise error(422, "unsupported_format", "only meiko.saver.v1 exports can be imported")
    from meiko.plugins.builtin.saver.state import SavedPost, SaverConfig
    from meiko.plugins.support.state import read_mirror, write_mirror

    scope = _scope(bot_id)
    current = SaverConfig.from_dict(read_mirror(SAVER_SLUG, scope, "saver:config", {}))
    merged = SaverConfig.from_dict({**payload.config, **current.to_dict()})  # local values win
    write_mirror(SAVER_SLUG, scope, "saver:config", merged.to_dict())

    existing = read_mirror(SAVER_SLUG, scope, "index:saved", []) or []
    seen = {
        (str(entry.get("chat")), int(entry.get("message_id") or 0))
        for entry in existing
        if isinstance(entry, dict)
    }
    added = 0
    for row in payload.index[:2000]:
        if not isinstance(row, dict):
            continue
        post = SavedPost.from_dict(row)
        key = (post.chat, post.message_id)
        if key in seen or not post.chat:
            continue
        seen.add(key)
        existing.append(post.to_dict())
        added += 1
    write_mirror(SAVER_SLUG, scope, "index:saved", existing[:2000])
    await _reload_plugin(bot_id, SAVER_SLUG)
    return {"imported": added, "skipped": len(payload.index) - added, "total": len(existing)}


# ------------------------------------------------------- encoder profile
@router.get("/encoder/profile")
async def encoder_profile(auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    from meiko.plugins.support.state import read_mirror

    return {"profile": read_mirror("encoder", _scope(bot_id), "profile", {}) or {}, "tools": _encoder_tools()}


class ProfileRequest(BaseModel):
    codec: str = "x265"
    crf: int = Field(default=26, ge=0, le=51)
    preset: str = "medium"
    container: str = "mkv"
    resolution: str = "original"
    audio_codec: str = "copy"
    audio_bitrate: str = "128k"
    channels: str = "stereo"
    subtitles: str = "copy"
    ten_bit: bool = False
    watermark: str = ""
    watermark_position: str = "top-left"
    thumbnail: bool = True


@router.patch("/encoder/profile")
async def encoder_set_profile(
    payload: ProfileRequest, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    from meiko.plugins.builtin.encoder.plans import AUDIO_CODECS, CODECS, CONTAINERS, PRESETS, RESOLUTIONS, SUBTITLE_MODES
    from meiko.plugins.support.state import read_mirror, write_mirror

    problems = []
    if payload.codec not in CODECS:
        problems.append(f"codec must be one of {', '.join(CODECS)}")
    if payload.preset not in PRESETS:
        problems.append(f"preset must be one of {', '.join(PRESETS)}")
    if payload.container not in CONTAINERS:
        problems.append(f"container must be one of {', '.join(CONTAINERS)}")
    if payload.resolution not in RESOLUTIONS:
        problems.append(f"resolution must be one of {', '.join(RESOLUTIONS)}")
    if payload.audio_codec not in AUDIO_CODECS:
        problems.append(f"audio codec must be one of {', '.join(AUDIO_CODECS)}")
    if payload.subtitles not in SUBTITLE_MODES:
        problems.append(f"subtitles must be one of {', '.join(SUBTITLE_MODES)}")
    if problems:
        raise error(422, "invalid_profile", "; ".join(problems))
    scope = _scope(bot_id)
    previous = read_mirror("encoder", scope, "profile", {}) or {}
    profile = {**payload.model_dump()}
    write_mirror("encoder", scope, "profile", profile)
    await _reload_plugin(bot_id, "encoder")
    return {"saved": True, "profile": profile, "previous": previous}


# --------------------------------------------------------- filestore admin
class ShopCreateRequest(BaseModel):
    bot_id: int
    title: str = ""


@router.post("/filestore/shops")
async def filestore_create_shop(
    payload: ShopCreateRequest, auth: CurrentAuth, session: SessionDep
) -> dict[str, Any]:
    await _require_bot(session, auth, payload.bot_id)
    try:
        shop = _filestore().register(
            _scope(payload.bot_id), owner_id=auth.user.id, bot_id=payload.bot_id, title=payload.title
        )
    except ShopError as exc:
        raise error(409, "conflict", str(exc)) from exc
    return {"shop": shop.to_dict(include_files=False) | {"usage": shop.usage()}}


class ShopSettingsRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    messages: dict[str, str] = Field(default_factory=dict)


@router.patch("/filestore/{slug}/settings")
async def filestore_settings(
    slug: str, payload: ShopSettingsRequest, auth: CurrentAuth, session: SessionDep
) -> dict[str, Any]:
    service = _filestore()
    for bot_id in await _owned_bot_ids(session, auth):
        shop = service.for_bot(_scope(bot_id), bot_id)
        if shop is None or shop.slug != slug:
            continue
        try:
            for key, value in payload.settings.items():
                shop = service.set_setting(_scope(bot_id), slug, key, value, user_id=auth.user.id)
            for key, value in payload.messages.items():
                shop = service.set_message(_scope(bot_id), slug, key, str(value), user_id=auth.user.id)
        except ShopError as exc:
            raise error(403, "forbidden", str(exc)) from exc
        return {"saved": True, "shop": shop.to_dict(include_files=False)}
    raise error(404, "not_found", "filestore not found")


@router.get("/filestore/{slug}/users")
async def filestore_users(slug: str, auth: CurrentAuth, session: SessionDep) -> dict[str, Any]:
    service = _filestore()
    for bot_id in await _owned_bot_ids(session, auth):
        shop = service.for_bot(_scope(bot_id), bot_id)
        if shop is None or shop.slug != slug:
            continue
        if not shop.can_manage(auth.user.id):
            raise error(403, "forbidden", "only the owner or an admin can see users")
        return {
            "users": [{"user_id": int(uid), **row} for uid, row in shop.users.items()],
            "admins": shop.admins,
            "banned": shop.banned,
        }
    raise error(404, "not_found", "filestore not found")


class ShopAdminRequest(BaseModel):
    user_id: int
    action: str = "add_admin"  # add_admin | remove_admin | ban | unban


@router.post("/filestore/{slug}/admins")
async def filestore_admins(
    slug: str, payload: ShopAdminRequest, auth: CurrentAuth, session: SessionDep
) -> dict[str, Any]:
    service = _filestore()
    for bot_id in await _owned_bot_ids(session, auth):
        shop = service.for_bot(_scope(bot_id), bot_id)
        if shop is None or shop.slug != slug:
            continue
        scope = _scope(bot_id)
        try:
            if payload.action == "add_admin":
                shop = service.add_admin(scope, slug, payload.user_id, actor_id=auth.user.id)
            elif payload.action == "remove_admin":
                shop = service.remove_admin(scope, slug, payload.user_id, actor_id=auth.user.id)
            elif payload.action in {"ban", "unban"}:
                shop = service.ban(
                    scope, slug, payload.user_id, actor_id=auth.user.id, banned=payload.action == "ban"
                )
            else:
                raise error(422, "invalid_action", "action must be add_admin/remove_admin/ban/unban")
        except ShopError as exc:
            raise error(403, "forbidden", str(exc)) from exc
        return {"admins": shop.admins, "banned": shop.banned}
    raise error(404, "not_found", "filestore not found")


# ------------------------------------------------- linkshare batch/settings
class BatchRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)
    ttl_hours: int = 24
    max_claims: int = 0


@router.post("/linkshare/batch")
async def linkshare_batch(
    payload: BatchRequest, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    batch, links = _linkshare().create_batch(
        _scope(bot_id),
        urls=payload.urls,
        owner_id=auth.user.id,
        ttl_hours=payload.ttl_hours,
        max_claims=payload.max_claims,
        bot_id=bot_id,
    )
    return {"batch": batch, "links": [link.to_dict() for link in links]}


class LinkSettingsRequest(BaseModel):
    default_ttl_hours: int | None = None
    default_max_claims: int | None = None
    shortener_base: str | None = None


@router.get("/linkshare/settings")
async def linkshare_settings(auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    stored = get_module_store().read("linkshare", f"settings:{_scope(bot_id)}")
    return {
        "settings": stored,
        "defaults": {"default_ttl_hours": 24, "default_max_claims": 0, "shortener_base": ""},
    }


@router.patch("/linkshare/settings")
async def linkshare_set_settings(
    payload: LinkSettingsRequest, auth: CurrentAuth, session: SessionDep, bot_id: int
) -> dict[str, Any]:
    await _require_bot(session, auth, bot_id)
    patch = {key: value for key, value in payload.model_dump().items() if value is not None}
    stored = get_module_store().update("linkshare", f"settings:{_scope(bot_id)}", patch)
    return {"saved": True, "settings": stored}


# --------------------------------------------------------------- leech jobs
@router.post("/leech/jobs/{job_id}/retry")
async def leech_retry(job_id: str, auth: CurrentAuth, session: SessionDep, bot_id: int) -> dict[str, Any]:
    """Re-queue through the running bot; never fake a retry without a worker."""
    await _require_bot(session, auth, bot_id)
    from meiko.runtime.manager import get_worker_manager

    application = get_worker_manager().state.applications.get(bot_id)
    if application is None:
        return {"retried": False, "reason": "the bot is not running; start it to retry transfers"}
    for plugin in getattr(application, "plugins", None) or []:
        if getattr(plugin, "slug", "") != "leech":
            continue
        retry = getattr(plugin, "retry_task", None)
        if retry is None:
            break
        outcome = retry(job_id)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return {"retried": bool(outcome), "job_id": job_id}
    return {"retried": False, "reason": "the leech plugin is not loaded for this bot"}


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
