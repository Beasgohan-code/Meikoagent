"""Marketplace service: sources, packages, versions, approvals.

The rule this module exists to enforce is that *approval is not execution*.  A
package can be imported, inspected, reviewed and approved without a single bot
ever loading it, and a bot can keep running an older approved version while a
newer one waits for review.  That is why the state machine is spread over three
tables instead of one ``status`` column:

    PluginSource   -> where the code may come from (owner-registered, pinnable)
    PluginPackage  -> one plugin identity, its review state
    PluginVersion  -> one pinned commit + checksum, its approval
    PluginInstallation -> one bot/group/global activation of a version

``PLATFORM_PACKAGE_STATES`` below is the brief's state model; the transitions are
enforced here rather than in the router so a new endpoint cannot skip a step.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meiko.db.models import PluginPackage, PluginSource, PluginVersion, User
from meiko.plugins.marketplace import Inspection, validate_source_url
from meiko.services.core import NotFound, ValidationError, utcnow

#: AVAILABLE -> REVIEW_PENDING -> APPROVED -> PLATFORM_INSTALLED -> REMOVED.
#: REJECTED / BLOCKED are terminal until an owner re-opens the package.
PLATFORM_PACKAGE_STATES = (
    "available",
    "review_pending",
    "approved",
    "platform_installed",
    "rejected",
    "blocked",
    "removed",
)

PACKAGE_TRANSITIONS: dict[str, set[str]] = {
    "available": {"review_pending", "blocked", "removed"},
    "review_pending": {"approved", "rejected", "blocked"},
    "approved": {"platform_installed", "blocked", "removed"},
    "platform_installed": {"removed", "blocked", "approved"},
    "rejected": {"review_pending", "blocked"},
    "blocked": {"review_pending"},
    "removed": {"review_pending"},
}

VERSION_APPROVALS = ("pending", "approved", "rejected", "withdrawn")


class MarketplaceError(ValidationError):
    """A marketplace transition was refused."""


class MarketplaceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- sources ---------------------------------------------------------
    async def add_source(
        self,
        *,
        name: str,
        url: str,
        ref: str = "main",
        license: str = "",
        added_by: int | None = None,
        trust: str = "untrusted",
    ) -> PluginSource:
        clean_url = validate_source_url(url)
        existing = await self.session.scalar(
            select(PluginSource).where(PluginSource.url == clean_url)
        )
        if existing is not None:
            raise MarketplaceError("this source is already registered")
        from meiko.plugins.marketplace import APPROVED_LICENCES, normalise_ref

        ref = normalise_ref(ref)
        license_ok = license.strip().lower() in APPROVED_LICENCES if license else False
        source = PluginSource(
            name=name.strip()[:120] or clean_url.rsplit("/", 1)[-1],
            url=clean_url,
            kind="git",
            ref=ref,
            license=license.strip()[:64],
            license_ok=license_ok,
            trust=trust if trust in ("trusted", "untrusted") else "untrusted",
            added_by=added_by,
        )
        self.session.add(source)
        await self.session.flush()
        return source

    async def get_source(self, source_id: int) -> PluginSource:
        source = await self.session.get(PluginSource, source_id)
        if source is None:
            raise NotFound("source not found")
        return source

    async def list_sources(self) -> list[PluginSource]:
        return list((await self.session.scalars(select(PluginSource).order_by(PluginSource.id))).all())

    async def disable_source(self, source_id: int) -> PluginSource:
        source = await self.get_source(source_id)
        source.enabled = False
        await self.session.flush()
        return source

    # -- packages --------------------------------------------------------
    async def get_package(self, package_id: int) -> PluginPackage:
        package = await self.session.get(PluginPackage, package_id)
        if package is None:
            raise NotFound("package not found")
        return package

    async def list_packages(self, *, state: str | None = None) -> list[PluginPackage]:
        stmt = select(PluginPackage).order_by(PluginPackage.slug)
        if state:
            stmt = stmt.where(PluginPackage.state == state)
        return list((await self.session.scalars(stmt)).all())

    async def import_inspection(
        self,
        *,
        source_id: int,
        inspection: Inspection,
        path: str = "",
    ) -> tuple[PluginPackage, PluginVersion]:
        """Persist an inspection result as a package + pinned version.

        Importing does **not** approve anything: the package lands in
        ``review_pending`` (or ``blocked`` when the pipeline found an error) and
        the version in ``pending``.
        """
        source = await self.get_source(source_id)
        if not source.enabled:
            raise MarketplaceError("source is disabled")
        manifest = inspection.manifest or {}
        slug = str(manifest.get("slug") or "").strip()
        if not slug:
            raise MarketplaceError("the inspection produced no manifest slug")
        package = await self.session.scalar(
            select(PluginPackage).where(
                PluginPackage.source_id == source_id, PluginPackage.slug == slug
            )
        )
        blocked = inspection.blocked
        if package is None:
            package = PluginPackage(
                source_id=source_id,
                slug=slug,
                name=str(manifest.get("name") or slug),
                path=path,
                entrypoint=str(manifest.get("entry") or ""),
                state="blocked" if blocked else "review_pending",
                blocked_reason="; ".join(
                    f.message for f in inspection.findings if f.severity in ("error", "critical")
                )
                if blocked
                else "",
            )
            self.session.add(package)
            await self.session.flush()
        else:
            package.name = str(manifest.get("name") or package.name)
            package.entrypoint = str(manifest.get("entry") or package.entrypoint)
            package.state = "blocked" if blocked else "review_pending"
            package.blocked_reason = (
                "; ".join(
                    f.message for f in inspection.findings if f.severity in ("error", "critical")
                )
                if blocked
                else ""
            )
            await self.session.flush()

        version = await self.session.scalar(
            select(PluginVersion).where(
                PluginVersion.package_id == package.id,
                PluginVersion.version == str(manifest.get("version") or "0.0.0"),
            )
        )
        payload = {
            "commit_sha": inspection.commit,
            "checksum": str(manifest.get("checksum") or ""),
            "manifest": manifest,
            "inspection": inspection.to_dict(),
            "licence": inspection.licence,
        }
        if version is None:
            version = PluginVersion(
                package_id=package.id,
                version=str(manifest.get("version") or "0.0.0"),
                approval="pending",
                **payload,
            )
            self.session.add(version)
        else:
            for key, value in payload.items():
                setattr(version, key, value)
        await self.session.flush()
        return package, version

    async def transition_package(
        self, package: PluginPackage, state: str, *, actor_user_id: int | None = None
    ) -> PluginPackage:
        if state not in PLATFORM_PACKAGE_STATES:
            raise MarketplaceError(f"unknown package state {state!r}")
        if state == package.state:
            return package
        allowed = PACKAGE_TRANSITIONS.get(package.state, set())
        if state not in allowed:
            raise MarketplaceError(f"cannot move package from {package.state} to {state}")
        package.state = state
        if state in ("approved", "rejected"):
            package.reviewed_by = actor_user_id
            package.reviewed_at = utcnow()
        if state in ("review_pending", "approved"):
            package.blocked_reason = ""
        await self.session.flush()
        return package

    # -- versions --------------------------------------------------------
    async def latest_version(self, package_id: int) -> PluginVersion | None:
        versions = (
            await self.session.scalars(
                select(PluginVersion)
                .where(PluginVersion.package_id == package_id)
                .order_by(PluginVersion.id.desc())
            )
        ).all()
        return versions[0] if versions else None

    async def approve_version(
        self,
        version: PluginVersion,
        *,
        actor_user_id: int | None,
        publish: bool = True,
    ) -> PluginVersion:
        """Approve a version. Approval is a record, not a deployment."""
        if version.approval == "approved" and version.published == publish:
            return version
        if not version.commit_sha:
            raise MarketplaceError("a version without a pinned commit cannot be approved")
        version.approval = "approved"
        version.approved_by = actor_user_id
        version.approved_at = utcnow()
        version.published = publish
        package = await self.get_package(version.package_id)
        if package.state == "available":
            await self.transition_package(package, "review_pending")
        await self.session.flush()
        return version

    async def reject_version(
        self, version: PluginVersion, *, actor_user_id: int | None, reason: str = ""
    ) -> PluginVersion:
        version.approval = "rejected"
        version.published = False
        version.approved_by = actor_user_id
        version.approved_at = utcnow()
        version.rejection_reason = reason[:2000]
        await self.session.flush()
        # Rejecting the only version should also move the package, otherwise the
        # marketplace keeps advertising something that cannot be installed.
        package = await self.get_package(version.package_id)
        if package.state in ("review_pending", "available"):
            await self.transition_package(package, "rejected", actor_user_id=actor_user_id)
        return version

    async def transition_version(self, version: PluginVersion, approval: str) -> PluginVersion:
        if approval not in VERSION_APPROVALS:
            raise MarketplaceError(f"unknown approval state {approval!r}")
        version.approval = approval
        await self.session.flush()
        return version

    # -- queries used by the install path --------------------------------
    async def installable_version(self, slug: str) -> PluginVersion | None:
        """The newest approved, published, non-blocked version of a package."""
        package = await self.session.scalar(
            select(PluginPackage).where(PluginPackage.slug == slug)
        )
        if package is None or package.state in ("blocked", "rejected", "removed"):
            return None
        versions = (
            await self.session.scalars(
                select(PluginVersion)
                .where(
                    PluginVersion.package_id == package.id,
                    PluginVersion.approval == "approved",
                    PluginVersion.published.is_(True),
                )
                .order_by(PluginVersion.id.desc())
            )
        ).all()
        return versions[0] if versions else None

    async def summary(self) -> dict[str, Any]:
        sources = await self.list_sources()
        packages = await self.list_packages()
        versions = (await self.session.scalars(select(PluginVersion))).all()
        return {
            "sources": len(sources),
            "packages": len(packages),
            "versions": len(versions),
            "by_state": {
                state: sum(1 for package in packages if package.state == state)
                for state in PLATFORM_PACKAGE_STATES
            },
            "pending_review": sum(1 for v in versions if v.approval == "pending"),
            "approved": sum(1 for v in versions if v.approval == "approved"),
            "blocked": sum(1 for package in packages if package.state == "blocked"),
        }

    async def reviewer(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)
