"""Owner control center API – platform administration, enforced server side."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from meiko.api.deps import OwnerAuth, SessionDep, error
from meiko.api.schemas import UserResponse
from meiko.db.models import (
    AuditLog,
    BotAccount,
    PluginInstallation,
    RuntimeEvent,
    User,
    Worker,
)
from meiko.services.core import AuditService, UserService

router = APIRouter(prefix="/owner", tags=["owner"])


@router.get("/overview", response_model=dict)
async def overview(auth: OwnerAuth, session: SessionDep) -> dict[str, Any]:
    users = int(await session.scalar(select(func.count()).select_from(User)) or 0)
    bots = int(await session.scalar(select(func.count()).select_from(BotAccount)) or 0)
    running = int(
        await session.scalar(
            select(func.count()).select_from(BotAccount).where(BotAccount.status == "running")
        )
        or 0
    )
    plugins = int(await session.scalar(select(func.count()).select_from(PluginInstallation)) or 0)
    errors = int(
        await session.scalar(
            select(func.count())
            .select_from(RuntimeEvent)
            .where(RuntimeEvent.severity.in_(["error", "critical"]))
        )
        or 0
    )
    workers = (
        await session.scalars(select(Worker).order_by(Worker.id.desc()).limit(10))
    ).all()
    return {
        "users": users,
        "bots": bots,
        "running_bots": running,
        "plugin_installations": plugins,
        "runtime_errors": errors,
        "workers": [
            {
                "id": w.id,
                "name": w.name,
                "status": w.status,
                "load": f"{w.current_load}/{w.capacity}",
            }
            for w in workers
        ],
    }


@router.get("/users", response_model=list[UserResponse])
async def users(
    auth: OwnerAuth, session: SessionDep, search: str | None = None, limit: int = 50
) -> list[UserResponse]:
    stmt = select(User).order_by(User.id.desc()).limit(min(limit, 200))
    if search:
        like = f"%{search.lstrip('@')}%"
        stmt = stmt.where((User.username.ilike(like)) | (User.first_name.ilike(like)))
    rows = (await session.scalars(stmt)).all()
    return [UserResponse.model_validate(row) for row in rows]


@router.post("/users/{user_id}/role", response_model=UserResponse)
async def set_role(
    user_id: int, role: str, auth: OwnerAuth, session: SessionDep
) -> UserResponse:
    if role not in ("user", "admin", "owner"):
        raise error(422, "invalid_role", "role must be user, admin or owner")
    user = await UserService(session).set_role(user_id, role, actor_is_owner=True)
    await AuditService(session).log(
        "owner.role_changed", actor_user_id=auth.user.id, target_type="user", target_id=user_id,
        metadata={"role": role},
    )
    return UserResponse.model_validate(user)


@router.post("/users/{user_id}/status", response_model=UserResponse)
async def set_status(
    user_id: int, status: str, auth: OwnerAuth, session: SessionDep
) -> UserResponse:
    if status not in ("active", "restricted"):
        raise error(422, "invalid_status", "status must be active or restricted")
    user = await UserService(session).get(user_id)
    user.status = status
    await AuditService(session).log(
        "owner.user_status", actor_user_id=auth.user.id, target_type="user", target_id=user_id,
        metadata={"status": status},
    )
    return UserResponse.model_validate(user)


@router.get("/bots", response_model=list[dict])
async def all_bots(auth: OwnerAuth, session: SessionDep, limit: int = 100) -> list[dict]:
    rows = (
        await session.scalars(select(BotAccount).order_by(BotAccount.id.desc()).limit(min(limit, 500)))
    ).all()
    return [
        {
            "id": row.id,
            "owner_id": row.owner_id,
            "name": row.name,
            "username": row.username,
            "status": row.status,
            "is_clone": row.is_clone,
            "token_valid": row.token_valid,
            "last_error": row.last_error,
            "worker_id": row.worker_id,
        }
        for row in rows
    ]


@router.get("/audit", response_model=list[dict])
async def audit_log(
    auth: OwnerAuth, session: SessionDep, limit: int = 100
) -> list[dict]:
    rows = (
        await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500)))
    ).all()
    return [
        {
            "id": row.id,
            "actor_user_id": row.actor_user_id,
            "actor_type": row.actor_type,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "ip_address": row.ip_address,
            "created_at": row.created_at.isoformat(),
            "metadata": row.metadata_json,
        }
        for row in rows
    ]


@router.get("/security-events", response_model=list[dict])
async def security_events(auth: OwnerAuth, session: SessionDep) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=7)
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(
                AuditLog.created_at >= since,
                AuditLog.action.in_(["auth.login", "owner.role_changed", "owner.user_status"]),
            )
            .order_by(AuditLog.id.desc())
            .limit(100)
        )
    ).all()
    return [
        {
            "id": row.id,
            "action": row.action,
            "actor_user_id": row.actor_user_id,
            "ip_address": row.ip_address,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/plugins/{slug}/approve", response_model=dict)
async def approve_plugin(slug: str, auth: OwnerAuth, session: SessionDep) -> dict:
    from meiko.db.models import Plugin

    plugin = await session.scalar(select(Plugin).where(Plugin.slug == slug))
    if plugin is None:
        raise error(404, "not_found", "plugin not found")
    plugin.status = "approved"
    await AuditService(session).log(
        "owner.plugin_approved", actor_user_id=auth.user.id, target_type="plugin", target_id=slug
    )
    return {"slug": slug, "status": plugin.status}


@router.post("/plugins/{slug}/block", response_model=dict)
async def block_plugin(slug: str, auth: OwnerAuth, session: SessionDep) -> dict:
    from meiko.db.models import Plugin

    plugin = await session.scalar(select(Plugin).where(Plugin.slug == slug))
    if plugin is None:
        raise error(404, "not_found", "plugin not found")
    plugin.status = "blocked"
    installs = (
        await session.scalars(
            select(PluginInstallation).where(PluginInstallation.plugin_id == plugin.id)
        )
    ).all()
    for install in installs:
        install.state = "disabled"
    await AuditService(session).log(
        "owner.plugin_blocked", actor_user_id=auth.user.id, target_type="plugin", target_id=slug
    )
    return {"slug": slug, "status": plugin.status, "disabled_installations": len(installs)}


@router.post("/maintenance", response_model=dict)
async def maintenance(auth: OwnerAuth, enabled: bool = False) -> dict:
    from meiko.api.state import set_maintenance

    set_maintenance(enabled)
    return {"maintenance_mode": enabled}


# --------------------------------------------------------------- capabilities
CAPABILITIES = {
    "code_exec": {
        "label": "Code execution",
        "description": "Run python/node/bash snippets from chat inside the code_runner sandbox.",
        "risk": "critical",
        "plugin": "code_runner",
    },
    "remote_fetch": {
        "label": "Outbound HTTP",
        "description": "Let internet-facing plugins (integrations, music lookup) call external services.",
        "risk": "high",
        "plugin": "integrations",
    },
    "file_upload": {
        "label": "File storage",
        "description": "Persist uploaded files inside the plugin workspace quota.",
        "risk": "medium",
        "plugin": "file_store",
    },
    "media_encode": {
        "label": "Video encoding",
        "description": "Spawn ffmpeg for re-encodes (CPU-heavy, sizes the encoder workspace).",
        "risk": "high",
        "plugin": "encoder",
    },
    "bulk_transfer": {
        "label": "Bulk mirror/leech",
        "description": "Download remote files with the leech engine and re-upload them.",
        "risk": "high",
        "plugin": "leech",
    },
    "public_sharing": {
        "label": "Public sharing",
        "description": "Allow signed share links (filestore) and protected link release (linkshare).",
        "risk": "medium",
        "plugin": "filestore",
    },
}


@router.get("/capabilities", response_model=list[dict])
async def capabilities(auth: OwnerAuth, session: SessionDep) -> list[dict[str, Any]]:
    """Owner-visible state of every gated capability."""
    from meiko.plugins.support import CapabilityGate

    out = []
    for name, meta in CAPABILITIES.items():
        state = await CapabilityGate(name).state(refresh=True)
        out.append({**meta, "capability": name, **state.to_dict()})
    return out


@router.post("/capabilities/{capability}", response_model=dict)
async def set_capability(
    capability: str,
    auth: OwnerAuth,
    session: SessionDep,
    enabled: bool | None = None,
    allowed_telegram_ids: str | None = None,
    allowed_bot_ids: str | None = None,
    max_runs_per_day: int | None = None,
) -> dict[str, Any]:
    """Open, close or narrow a dangerous capability. Owner only, fully audited."""
    from meiko.plugins.support import CapabilityGate

    if capability not in CAPABILITIES:
        raise error(404, "not_found", "unknown capability")

    def parse_ids(raw: str | None) -> list[int] | None:
        if raw is None:
            return None
        return [int(part) for part in raw.replace(" ", "").split(",") if part.strip().lstrip("-").isdigit()]

    state = await CapabilityGate(capability).set_state(
        enabled=enabled,
        allowed_telegram_ids=parse_ids(allowed_telegram_ids),
        allowed_bot_ids=parse_ids(allowed_bot_ids),
        max_runs_per_day=max_runs_per_day,
        actor_user_id=auth.user.id,
    )
    await AuditService(session).log(
        "owner.capability_changed",
        actor_user_id=auth.user.id,
        target_type="capability",
        target_id=capability,
        metadata={
            "enabled": state.enabled,
            "allowed_telegram_ids": state.allowed_telegram_ids,
            "allowed_bot_ids": state.allowed_bot_ids,
        },
    )
    return {"capability": capability, **CAPABILITIES[capability], **state.to_dict()}


# ------------------------------------------------------- global plugin scope
# Global installations are platform-wide: they apply to every bot unless that bot
# has its own row.  They are therefore an owner capability, with their own
# endpoints so the ordinary marketplace client never needs a global code path.
@router.get("/plugins/global", response_model=list[dict])
async def global_installations(auth: OwnerAuth, session: SessionDep) -> list[dict[str, Any]]:
    """Every global-scope installation, with its live runtime state."""
    from meiko.db.models import Plugin
    from meiko.plugins.manager import runtime_status
    from meiko.runtime.manager import get_worker_manager

    from meiko.plugins.context import PluginScope

    rows = (
        await session.scalars(
            select(PluginInstallation).where(PluginInstallation.bot_id.is_(None))
        )
    ).all()
    catalogue = {plugin.id: plugin for plugin in (await session.scalars(select(Plugin))).all()}
    manager = get_worker_manager()
    out: list[dict[str, Any]] = []
    for row in rows:
        plugin = catalogue.get(row.plugin_id)
        live = any(
            application.plugins is not None
            and plugin is not None
            and application.plugins.is_running(plugin.slug, PluginScope(bot_id=bot_id))
            for bot_id, application in manager.state.applications.items()
        )
        out.append(
            {
                "id": row.id,
                "slug": plugin.slug if plugin else None,
                "name": plugin.name if plugin else None,
                "state": row.state,
                "version": row.version,
                "scope": row.scope,
                "config": row.config or {},
                "running_somewhere": live,
                "status": runtime_status(
                    plugin=plugin,
                    record=row,
                    live=live,
                    bot_running=bool(manager.state.applications),
                )
                if plugin
                else "blocked",
            }
        )
    return out


@router.post("/plugins/global/{slug}/reconcile", response_model=dict)
async def reconcile_global_plugin(slug: str, auth: OwnerAuth, session: SessionDep) -> dict[str, Any]:
    """Re-apply a global plugin to every running bot, right now.

    "Global changes apply on next start" was the old behaviour and it silently
    left running bots on stale plugin sets.  This reconciles them immediately and
    reports per-bot results, including the bots it could not touch.
    """
    from meiko.db.models import Plugin
    from meiko.runtime.manager import get_worker_manager

    plugin = await session.scalar(select(Plugin).where(Plugin.slug == slug))
    if plugin is None:
        raise error(404, "not_found", "plugin not found")
    manager = get_worker_manager()
    results: dict[str, Any] = {}
    for bot_id, application in list(manager.state.applications.items()):
        if application.plugins is None:
            results[str(bot_id)] = {"applied": False, "reason": "plugins not loaded"}
            continue
        results[str(bot_id)] = {"applied": True, "states": await application.load_plugins()}
    await AuditService(session).log(
        "owner.plugin_global_reconciled",
        actor_user_id=auth.user.id,
        target_type="plugin",
        target_id=slug,
        metadata={"bots": list(results), "applied": sum(
            1 for value in results.values() if value.get("applied")
        )},
    )
    return {
        "slug": slug,
        "bots": results,
        "applied": sum(1 for value in results.values() if value.get("applied")),
        "skipped": sum(1 for value in results.values() if not value.get("applied")),
    }


@router.get("/plugins/overview", response_model=dict)
async def plugin_overview(auth: OwnerAuth, session: SessionDep) -> dict[str, Any]:
    """Owner-facing plugin inventory: catalogue, approvals, activations, failures."""
    from meiko.db.models import Plugin
    from meiko.plugins.base import registry as plugin_registry
    from meiko.plugins.manager import missing_permissions, permission_changes, runtime_status

    plugins = (await session.scalars(select(Plugin).order_by(Plugin.slug))).all()
    installs = (await session.scalars(select(PluginInstallation))).all()
    by_plugin: dict[int, list[PluginInstallation]] = {}
    for row in installs:
        by_plugin.setdefault(row.plugin_id, []).append(row)
    implemented = set(plugin_registry.slugs())
    rows: list[dict[str, Any]] = []
    for plugin in plugins:
        rows_of = by_plugin.get(plugin.id, [])
        rows.append(
            {
                "slug": plugin.slug,
                "name": plugin.name,
                "version": plugin.version,
                "category": plugin.category,
                "status": plugin.status,
                "implemented": plugin.slug in implemented,
                "permissions": list(plugin.permissions or []),
                "missing_permissions": missing_permissions(plugin),
                "pending_permission_changes": permission_changes(plugin),
                "dependencies": list(plugin.dependencies or []),
                "installations": {
                    "global": sum(1 for r in rows_of if r.bot_id is None),
                    "bot": sum(1 for r in rows_of if r.bot_id is not None and r.group_id is None),
                    "group": sum(1 for r in rows_of if r.group_id is not None),
                },
                "states": {
                    state: sum(1 for r in rows_of if r.state == state)
                    for state in sorted({r.state for r in rows_of})
                },
            }
        )
    return {
        "plugins": rows,
        "totals": {
            "catalogue": len(plugins),
            "implemented": sum(1 for r in rows if r["implemented"]),
            "approved": sum(1 for r in plugins if r.status == "approved"),
            "blocked": sum(1 for r in plugins if r.status == "blocked"),
            "pending": sum(1 for r in plugins if r.status == "pending"),
            "installations": len(installs),
            "global_installations": sum(1 for r in installs if r.bot_id is None),
        },
    }


# --------------------------------------------------------------- marketplace
# Everything below is the import/review pipeline.  Note what is *not* here: no
# endpoint installs from a URL, runs a hook or executes imported code.  Importing
# stores findings; approving stores a decision; only the ordinary install path
# activates anything, and only for an approved + published version.
@router.get("/marketplace/overview", response_model=dict)
async def marketplace_overview(auth: OwnerAuth, session: SessionDep) -> dict[str, Any]:
    from meiko.services.marketplace import MarketplaceService

    service = MarketplaceService(session)
    summary = await service.summary()
    packages = await service.list_packages()
    versions = {}
    for package in packages:
        latest = await service.latest_version(package.id)
        versions[package.slug] = (
            {
                "version": latest.version,
                "approval": latest.approval,
                "published": latest.published,
                "commit": latest.commit_sha[:12],
                "licence": latest.licence,
            }
            if latest
            else None
        )
    return {
        "summary": summary,
        "packages": [
            {
                "id": package.id,
                "slug": package.slug,
                "name": package.name,
                "state": package.state,
                "blocked_reason": package.blocked_reason,
                "source_id": package.source_id,
                "latest": versions.get(package.slug),
            }
            for package in packages
        ],
        "sources": [
            {
                "id": source.id,
                "name": source.name,
                "url": source.url,
                "ref": source.ref,
                "licence": source.license,
                "licence_ok": source.license_ok,
                "trust": source.trust,
                "enabled": source.enabled,
            }
            for source in await service.list_sources()
        ],
    }


@router.post("/marketplace/sources", response_model=dict, status_code=201)
async def add_source(
    auth: OwnerAuth,
    session: SessionDep,
    name: str = "",
    url: str = "",
    ref: str = "main",
    license: str = "",
) -> dict[str, Any]:
    """Register a source repository. Only an owner can name a URL at all."""
    from meiko.services.marketplace import MarketplaceError, MarketplaceService

    try:
        source = await MarketplaceService(session).add_source(
            name=name, url=url, ref=ref, license=license, added_by=auth.user.id
        )
    except MarketplaceError as exc:
        raise error(422, "invalid_source", str(exc)) from exc
    except ValueError as exc:
        raise error(422, "invalid_source", str(exc)) from exc
    await AuditService(session).log(
        "owner.marketplace_source_added",
        actor_user_id=auth.user.id,
        target_type="plugin_source",
        target_id=source.id,
        metadata={"url": source.url, "ref": source.ref},
    )
    return {"id": source.id, "name": source.name, "url": source.url, "ref": source.ref}


@router.post("/marketplace/sources/{source_id}/import", response_model=dict)
async def import_source(
    source_id: int,
    auth: OwnerAuth,
    session: SessionDep,
    path: str = "",
) -> dict[str, Any]:
    """Inspect a *local checkout* of a registered source and record the findings.

    The clone itself is an operator action (``deploy/import-plugin.sh`` or a CI
    job) because this process must never fetch and execute third-party code.  This
    endpoint only reads the directory it is pointed at: it lists files, parses one
    manifest, checks the licence and scans statically.  Nothing is imported,
    executed or installed here.
    """
    from pathlib import Path

    from meiko.config import get_settings
    from meiko.plugins.marketplace import inspect_directory
    from meiko.services.marketplace import MarketplaceError, MarketplaceService

    service = MarketplaceService(session)
    source = await service.get_source(source_id)
    root_dir = Path(get_settings().data_dir) / "plugin_sources" / str(source.id)
    if not root_dir.is_dir():
        raise error(
            404,
            "checkout_missing",
            f"no checkout at {root_dir}; clone the source there first (the API does not run git fetch)",
        )
    root = root_dir / path if path else root_dir
    try:
        resolved = root.resolve()
        if not str(resolved).startswith(str(root_dir.resolve())):
            raise error(422, "invalid_path", "path escapes the source checkout")
    except OSError as exc:  # pragma: no cover - filesystem dependent
        raise error(422, "invalid_path", str(exc)) from exc

    inspection = inspect_directory(resolved, platform_version="1.0.0")
    if not inspection.commit:
        from meiko.plugins.marketplace import resolve_commit

        try:
            inspection.commit = resolve_commit(resolved, source.ref)
        except ValueError as exc:
            inspection.add("error", "commit.unresolved", str(exc))
    try:
        package, version = await service.import_inspection(
            source_id=source.id, inspection=inspection, path=path
        )
    except MarketplaceError as exc:
        raise error(422, "import_failed", str(exc)) from exc
    await AuditService(session).log(
        "owner.marketplace_imported",
        actor_user_id=auth.user.id,
        target_type="plugin_package",
        target_id=package.slug,
        metadata={
            "source_id": source.id,
            "commit": inspection.commit,
            "findings": len(inspection.findings),
            "blocked": inspection.blocked,
            "state": package.state,
        },
    )
    return {
        "package": {"id": package.id, "slug": package.slug, "state": package.state},
        "version": {
            "id": version.id,
            "version": version.version,
            "approval": version.approval,
            "commit": version.commit_sha,
        },
        "inspection": inspection.to_dict(),
    }


@router.get("/marketplace/packages/{slug}", response_model=dict)
async def package_detail(slug: str, auth: OwnerAuth, session: SessionDep) -> dict[str, Any]:
    from sqlalchemy import select as _select

    from meiko.db.models import PluginPackage, PluginVersion
    from meiko.services.marketplace import MarketplaceService

    service = MarketplaceService(session)
    package = await session.scalar(_select(PluginPackage).where(PluginPackage.slug == slug))
    if package is None:
        raise error(404, "not_found", "package not found")
    versions = (
        await session.scalars(
            _select(PluginVersion)
            .where(PluginVersion.package_id == package.id)
            .order_by(PluginVersion.id.desc())
        )
    ).all()
    return {
        "id": package.id,
        "slug": package.slug,
        "name": package.name,
        "state": package.state,
        "blocked_reason": package.blocked_reason,
        "review_notes": package.review_notes,
        "source_id": package.source_id,
        "versions": [
            {
                "id": version.id,
                "version": version.version,
                "approval": version.approval,
                "published": version.published,
                "commit": version.commit_sha,
                "checksum": version.checksum,
                "licence": version.licence,
                "inspection": version.inspection,
            }
            for version in versions
        ],
        "installable": await service.installable_version(slug) is not None,
    }


@router.post("/marketplace/packages/{slug}/approve", response_model=dict)
async def approve_package(
    slug: str,
    auth: OwnerAuth,
    session: SessionDep,
    version_id: int | None = None,
    publish: bool = True,
    notes: str = "",
) -> dict[str, Any]:
    """Approve one pinned version for installation. Separate from execution."""
    from sqlalchemy import select as _select

    from meiko.db.models import PluginPackage, PluginVersion
    from meiko.services.marketplace import MarketplaceError, MarketplaceService

    service = MarketplaceService(session)
    package = await session.scalar(_select(PluginPackage).where(PluginPackage.slug == slug))
    if package is None:
        raise error(404, "not_found", "package not found")
    if version_id is not None:
        version = await session.get(PluginVersion, version_id)
        if version is None or version.package_id != package.id:
            raise error(404, "not_found", "version not found")
    else:
        version = await service.latest_version(package.id)
        if version is None:
            raise error(404, "not_found", "this package has no imported version")
    inspection = dict(version.inspection or {})
    if not inspection.get("ok"):
        raise error(
            422,
            "inspection_blocked",
            "the inspection did not pass; fix the findings or record an override first",
        )
    try:
        version = await service.approve_version(
            version, actor_user_id=auth.user.id, publish=publish
        )
        package.review_notes = notes[:2000]
        package = await service.transition_package(
            package, "approved", actor_user_id=auth.user.id
        )
    except MarketplaceError as exc:
        raise error(422, "approval_failed", str(exc)) from exc
    await AuditService(session).log(
        "owner.marketplace_approved",
        actor_user_id=auth.user.id,
        target_type="plugin_package",
        target_id=slug,
        metadata={"version": version.version, "commit": version.commit_sha, "published": publish},
    )
    return {
        "slug": slug,
        "package_state": package.state,
        "version": version.version,
        "approval": version.approval,
        "published": version.published,
    }


@router.post("/marketplace/packages/{slug}/reject", response_model=dict)
async def reject_package(
    slug: str,
    auth: OwnerAuth,
    session: SessionDep,
    version_id: int | None = None,
    reason: str = "",
) -> dict[str, Any]:
    from sqlalchemy import select as _select

    from meiko.db.models import PluginPackage, PluginVersion
    from meiko.services.marketplace import MarketplaceError, MarketplaceService

    service = MarketplaceService(session)
    package = await session.scalar(_select(PluginPackage).where(PluginPackage.slug == slug))
    if package is None:
        raise error(404, "not_found", "package not found")
    version = (
        await session.get(PluginVersion, version_id)
        if version_id is not None
        else await service.latest_version(package.id)
    )
    if version is None:
        raise error(404, "not_found", "version not found")
    try:
        version = await service.reject_version(
            version, actor_user_id=auth.user.id, reason=reason
        )
    except MarketplaceError as exc:
        raise error(422, "rejection_failed", str(exc)) from exc
    await AuditService(session).log(
        "owner.marketplace_rejected",
        actor_user_id=auth.user.id,
        target_type="plugin_package",
        target_id=slug,
        metadata={"version": version.version, "reason": reason[:200]},
    )
    return {"slug": slug, "version": version.version, "approval": version.approval,
            "package_state": package.state}


# ------------------------------------------------------------ infrastructure
@router.get("/infrastructure", response_model=dict)
async def infrastructure(auth: OwnerAuth, session: SessionDep) -> dict[str, Any]:
    """Platform-wide wiring, for the owner view.

    Every entry comes from the component that owns it: the worker manager for
    bots, the queue for jobs, the toolchain resolver for binaries, the adapter for
    MTProto.  Nothing is inferred from "it is configured, so it is probably fine".
    """
    import shutil
    import time

    from meiko.cache import get_cache
    from meiko.config import get_settings
    from meiko.db.base import database_health
    from meiko.modules import mtproto, toolchain
    from meiko.runtime.manager import get_worker_manager

    settings = get_settings()
    manager = get_worker_manager()
    db = await database_health()
    cache = await get_cache().ping()
    disk = shutil.disk_usage(settings.data_dir)
    # The shared job queue lives in the worker/scheduler process.  When this API
    # process owns one, report it; otherwise say so instead of inventing numbers.
    try:
        from meiko.runtime.queue import get_queue  # type: ignore[attr-defined]

        queue = get_queue()
        queue_stats = {"available": True, **queue.stats()}
    except Exception:
        queue_stats = {
            "available": False,
            "reason": "the job queue is owned by the worker process; see /platform",
        }
    process: dict[str, Any] = {"uptime_seconds": None}
    try:
        import psutil  # type: ignore[import-not-found]

        proc = psutil.Process()
        process = {
            "uptime_seconds": int(time.time() - proc.create_time()),
            "cpu_percent": psutil.cpu_percent(interval=0.0),
            "rss_bytes": proc.memory_info().rss,
            "threads": proc.num_threads(),
            "source": "psutil",
        }
    except Exception:  # pragma: no cover - psutil optional
        process = {
            "uptime_seconds": None,
            "cpu_percent": None,
            "rss_bytes": None,
            "source": "unavailable (psutil not installed)",
        }

    workers = (await session.scalars(select(Worker).order_by(Worker.id))).all()
    return {
        "api": {
            "status": db["status"] if db["status"] == "ok" else "degraded",
            "workers_registered": len(workers),
            "uptime_seconds": process.get("uptime_seconds"),
        },
        "bot_workers": {
            "name": manager.state.name,
            "running_bots": len(manager.state.applications),
            "capacity": manager.state.capacity,
            "heartbeat_age_seconds": int(time.time() - manager.state.last_heartbeat)
            if manager.state.last_heartbeat
            else None,
            "ids": sorted(manager.state.applications),
        },
        "worker_rows": [
            {
                "id": row.id,
                "name": row.name,
                "status": row.status,
                "load": f"{row.current_load}/{row.capacity}",
                "hostname": row.hostname,
                "pid": row.pid,
            }
            for row in workers
        ],
        "scheduler": {
            "enabled": bool(settings.scheduler_enabled),
            # The scheduler object itself lives in the scheduler process; this is
            # the *configuration*, and the API does not pretend to observe ticks it
            # cannot see.
            "observed_here": False,
        },
        "watchdog": {
            "enabled": bool(settings.watchdog_enabled),
            "interval_seconds": settings.watchdog_interval_seconds,
        },
        "queue": queue_stats,
        "database": db,
        "redis": cache,
        "storage": {
            "data_dir": str(settings.data_dir),
            "free_bytes": disk.free,
            "total_bytes": disk.total,
            "used_percent": round(100 * (disk.total - disk.free) / disk.total, 1) if disk.total else None,
        },
        "ffmpeg": toolchain.describe(),
        "mtproto": mtproto.capabilities().to_dict(),
        "process": process,
        "background_tasks": {
            "polling_bots": len(manager.state.applications),
            # Stated as a count, not a claim about health.
            "note": "counts come from the worker manager; per-task detail is in /platform/logs",
        },
    }


@router.get("/security", response_model=dict)
async def security_overview(auth: OwnerAuth, session: SessionDep) -> dict[str, Any]:
    """Security posture: logins, failures, permission and credential changes."""
    from meiko.config import get_settings
    from meiko.db.models import ApiKey

    since = datetime.utcnow() - timedelta(days=7)
    interesting = [
        "auth.login",
        "auth.failed",
        "auth.api_key_created",
        "auth.api_key_revoked",
        "owner.role_changed",
        "owner.user_status",
        "owner.plugin_approved",
        "owner.plugin_blocked",
        "owner.marketplace_approved",
        "owner.marketplace_rejected",
        "owner.marketplace_source_added",
        "owner.capability_changed",
        "plugin.global_installed",
    ]
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.created_at >= since, AuditLog.action.in_(interesting))
            .order_by(AuditLog.id.desc())
            .limit(200)
        )
    ).all()
    bot_tokens = (
        await session.scalars(select(BotAccount).where(BotAccount.token_valid.is_(False)))
    ).all()
    active_keys = (
        await session.scalars(select(ApiKey).where(ApiKey.revoked_at.is_(None)))
    ).all()
    settings = get_settings()
    return {
        "window_days": 7,
        "events": [
            {
                "id": row.id,
                "action": row.action,
                "actor_user_id": row.actor_user_id,
                "target": f"{row.target_type}:{row.target_id}" if row.target_type else "",
                "ip_address": row.ip_address,
                "created_at": row.created_at.isoformat(),
                "metadata": row.metadata_json,
            }
            for row in rows
        ],
        "failed_auth": sum(1 for row in rows if row.action == "auth.failed"),
        "logins": sum(1 for row in rows if row.action == "auth.login"),
        "invalid_bot_tokens": len(bot_tokens),
        "active_api_keys": len(active_keys),
        "maintenance_mode": settings.maintenance_mode,
        "secrets": {
            # Never the value - only whether it is present, and where it comes from.
            "encryption_key_configured": bool(settings.encryption_key),
            "session_secret_configured": bool(getattr(settings, "secret_key", "")),
            "dev_auth_enabled": bool(settings.auth_dev_mode) and not settings.is_production,
        },
    }
