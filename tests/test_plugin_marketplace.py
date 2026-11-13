"""The safe import pipeline: inspect, never execute; approve, then install.

PART 3 of the brief asks for a real owner-controlled marketplace with a source ->
package -> version -> approval -> platform install -> activation chain, and above
all for an importer that **never runs imported code**.  These tests build real
directories on disk (with hostile content in them) and assert both halves: that
the pipeline notices, and that nothing was executed while noticing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from meiko.plugins.marketplace import (
    Inspection,
    detect_licence,
    find_manifest,
    inspect_directory,
    list_files,
    normalise_ref,
    scan_source,
    validate_source_url,
)

MIT_TEXT = """MIT License

Copyright (c) 2026 Somebody

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
"""

GPL_TEXT = """GNU GENERAL PUBLIC LICENSE
Version 3, 29 June 2007
"""


def write_repo(
    root: Path,
    *,
    slug: str = "imported_plugin",
    version: str = "1.0.0",
    licence: str | None = MIT_TEXT,
    extra_files: dict[str, str] | None = None,
    manifest_overrides: dict | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "slug": slug,
        "name": "Imported Plugin",
        "version": version,
        "category": "utilities",
        "description": "imported from a repository",
        "permissions": ["storage.read"],
        "dependencies": [],
        "config_schema": {},
        "checksum": "a" * 64,
        "source": "marketplace",
        "approved": True,
    }
    manifest.update(manifest_overrides or {})
    (root / "meiko_plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "plugin.py").write_text(
        "from meiko.plugins.base import MeikoPlugin\n\n\nclass Imported(MeikoPlugin):\n    pass\n",
        encoding="utf-8",
    )
    if licence is not None:
        (root / "LICENSE").write_text(licence, encoding="utf-8")
    for name, content in (extra_files or {}).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


class TestSourceUrlPolicy:
    def test_accepts_a_public_git_url(self):
        url = validate_source_url("https://github.com/owner/repo")
        assert url.startswith("https://github.com/")

    def test_refuses_private_hosts(self):
        with pytest.raises(ValueError):
            validate_source_url("http://127.0.0.1/repo")
        with pytest.raises(ValueError):
            validate_source_url("http://169.254.169.254/latest/meta-data")

    def test_refuses_file_scheme(self):
        with pytest.raises(ValueError):
            validate_source_url("file:///etc/passwd")

    def test_refuses_unknown_hosts(self):
        with pytest.raises(ValueError):
            validate_source_url("https://git.example.com/owner/repo")

    def test_refuses_credentials_in_url(self):
        with pytest.raises(ValueError):
            validate_source_url("https://user:secret@github.com/owner/repo")

    def test_ref_normalisation(self):
        assert normalise_ref("") == "main"
        assert normalise_ref("v1.2.3") == "v1.2.3"
        for bad in ("--upload-pack=evil", "../../etc", "a b", ";"):
            with pytest.raises(ValueError):
                normalise_ref(bad)


class TestStaticInspection:
    def test_clean_package_passes(self, tmp_path):
        root = write_repo(tmp_path / "clean")
        result = inspect_directory(root, commit="a" * 40)
        assert result.licence == "MIT"
        assert result.blocked is False, [f.to_dict() for f in result.findings]
        assert result.manifest["slug"] == "imported_plugin"
        assert result.execution_attempted is False
        assert any(f.code == "scan.completed" for f in result.findings)

    def test_missing_manifest_is_an_error(self, tmp_path):
        root = tmp_path / "none"
        root.mkdir()
        (root / "README.md").write_text("nothing here", encoding="utf-8")
        result = inspect_directory(root)
        assert result.blocked is True
        assert any(f.code == "manifest.missing" for f in result.findings)

    def test_missing_licence_is_an_error(self, tmp_path):
        root = write_repo(tmp_path / "nolicence", licence=None)
        result = inspect_directory(root)
        assert any(f.code == "licence.missing" for f in result.findings)
        assert result.blocked is True

    def test_gpl_licence_is_refused_without_override(self, tmp_path):
        root = write_repo(tmp_path / "gpl", licence=GPL_TEXT)
        result = inspect_directory(root)
        assert result.licence == "GPL"
        assert any(f.code == "licence.incompatible" and f.severity == "error" for f in result.findings)
        assert result.blocked is True

    def test_gpl_licence_allowed_only_with_owner_override(self, tmp_path):
        root = write_repo(tmp_path / "gpl2", licence=GPL_TEXT)
        result = inspect_directory(root, owner_licence_override=True)
        finding = next(f for f in result.findings if f.code == "licence.incompatible")
        assert finding.severity == "warning"

    def test_install_hooks_are_flagged_and_not_run(self, tmp_path):
        marker = tmp_path / "PWNED"
        root = write_repo(
            tmp_path / "hook",
            extra_files={
                # If anything executed this, the marker file would exist.
                "setup.py": f"open({str(marker)!r}, 'w').write('pwned')\n",
            },
        )
        result = inspect_directory(root)
        assert any(f.code == "file.install_hook" for f in result.findings)
        assert not marker.exists(), "inspection must never execute setup.py"

    def test_shell_hook_in_docstring_is_still_not_executed(self, tmp_path):
        marker = tmp_path / "ALSO_PWNED"
        root = write_repo(
            tmp_path / "hook2",
            extra_files={"install.sh": f"touch {marker}\n"},
        )
        inspect_directory(root)
        assert not marker.exists()

    def test_dangerous_patterns_are_found(self, tmp_path):
        root = write_repo(
            tmp_path / "danger",
            extra_files={
                "plugin.py": "import os\ndef go():\n    os.system('id')\n    eval('1+1')\n",
            },
        )
        result = inspect_directory(root)
        codes = {f.code for f in result.findings}
        assert "scan.rce" in codes
        assert "scan.eval" in codes
        assert result.blocked is True

    def test_obfuscated_payload_is_flagged(self, tmp_path):
        finding = scan_source(
            "payload = base64.b64decode('aGVsbG8gd29ybGQ=')\n", "x.py"
        )
        assert any(f.code == "scan.obfuscation" for f in finding)

    def test_hardcoded_credential_is_flagged(self, tmp_path):
        findings = scan_source('api_key = "sk-live-abcdef123456"\n', "cfg.py")
        assert any(f.code == "scan.secrets" for f in findings)

    def test_unlisted_dependency_is_an_error(self, tmp_path):
        root = write_repo(
            tmp_path / "deps",
            manifest_overrides={"dependencies": ["evil-package"]},
        )
        result = inspect_directory(root)
        assert any(f.code == "dependency.not_allowlisted" for f in result.findings)
        assert result.blocked is True

    def test_allowlisted_dependency_passes(self, tmp_path):
        root = write_repo(tmp_path / "deps2", manifest_overrides={"dependencies": ["httpx"]})
        result = inspect_directory(root)
        assert not any(f.code == "dependency.not_allowlisted" for f in result.findings)

    def test_unknown_permission_is_an_error(self, tmp_path):
        root = write_repo(
            tmp_path / "perms", manifest_overrides={"permissions": ["root.everything"]}
        )
        result = inspect_directory(root)
        assert any(f.code == "manifest.incompatible" for f in result.findings)

    def test_binary_blob_is_flagged(self, tmp_path):
        root = write_repo(
            tmp_path / "bin", extra_files={"data.bin": "binary-ish"}
        )
        result = inspect_directory(root)
        assert any(f.code == "file.unexpected_type" for f in result.findings)

    def test_manifest_can_live_below_the_root(self, tmp_path):
        root = tmp_path / "nested"
        write_repo(root / "packages" / "thing")
        manifest, reason = find_manifest(root)
        assert manifest is not None, reason
        assert manifest.parent.name == "thing"

    def test_ambiguous_manifests_are_refused(self, tmp_path):
        root = tmp_path / "ambiguous"
        write_repo(root / "a", slug="plugin_a")
        write_repo(root / "b", slug="plugin_b")
        manifest, reason = find_manifest(root)
        assert manifest is None
        assert "several manifests" in reason

    def test_list_files_skips_git_and_caches(self, tmp_path):
        root = write_repo(tmp_path / "listed")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("[core]", encoding="utf-8")
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
        names = list_files(root)
        assert not any(".git" in name or "__pycache__" in name for name in names)

    def test_detect_licence_variants(self, tmp_path):
        for name, text, expected in (
            ("LICENSE", MIT_TEXT, "MIT"),
            ("LICENSE.md", GPL_TEXT, "GPL"),
            ("COPYING", "Apache License\nVersion 2.0", "Apache-2.0"),
        ):
            root = tmp_path / f"lic-{name}"
            root.mkdir()
            (root / name).write_text(text, encoding="utf-8")
            assert detect_licence(root)[0] == expected


# --------------------------------------------------------------------------- #
# API-level: import -> review -> approve -> install gate
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture()
async def client(settings):
    from meiko.api.app import create_app
    from meiko.cache import reset_cache

    await reset_cache()
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def login(client: AsyncClient, telegram_id: int) -> tuple[str, dict]:
    response = await client.post(
        "/api/v1/auth/dev",
        json={"telegram_id": telegram_id, "username": f"u{telegram_id}", "first_name": "T"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["token"], body["user"]


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def make_owner(client: AsyncClient, telegram_id: int) -> str:
    from sqlalchemy import select

    from meiko.db.base import session_scope
    from meiko.db.models import User

    token, _ = await login(client, telegram_id)
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        user.is_owner = True
        user.role = "owner"
    return token


async def stage_checkout(settings, source_id: int, **kwargs) -> Path:
    """Materialise a checkout the way an operator would: a real git repo."""
    import subprocess

    root = write_repo(Path(settings.data_dir) / "plugin_sources" / str(source_id), **kwargs)
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "import"], check=True, env=env
    )
    return root


async def add_catalogue_row(slug: str, source: str = "marketplace"):
    """Register the catalogue row an imported package would publish."""
    from meiko.db.base import session_scope
    from meiko.db.models import Plugin

    from sqlalchemy import select

    async with session_scope() as session:
        existing = await session.scalar(select(Plugin).where(Plugin.slug == slug))
        if existing is not None:
            existing.source = source
            existing.status = "approved"
            return
        session.add(
            Plugin(
                slug=slug,
                name=slug.replace("_", " ").title(),
                category="utilities",
                source=source,
                status="approved",
            )
        )


class TestMarketplaceApi:
    async def test_source_registration_is_owner_only(self, client):
        token, _ = await login(client, 5001)
        response = await client.post(
            "/api/v1/owner/marketplace/sources?url=https://github.com/o/r&ref=main",
            headers=headers(token),
        )
        assert response.status_code == 403

    async def test_source_url_is_validated(self, client):
        owner = await make_owner(client, 5002)
        response = await client.post(
            "/api/v1/owner/marketplace/sources?url=http://169.254.169.254/x&ref=main",
            headers=headers(owner),
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_source"

    async def test_import_without_a_checkout_says_so(self, client):
        owner = await make_owner(client, 5003)
        created = await client.post(
            "/api/v1/owner/marketplace/sources?url=https://github.com/o/r&ref=main",
            headers=headers(owner),
        )
        source_id = created.json()["id"]
        response = await client.post(
            f"/api/v1/owner/marketplace/sources/{source_id}/import", headers=headers(owner)
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "checkout_missing"

    async def test_imported_package_needs_a_checkout_commit(self, client, settings):
        owner = await make_owner(client, 5004)
        created = await client.post(
            "/api/v1/owner/marketplace/sources?url=https://github.com/o/r&ref=v1.0.0",
            headers=headers(owner),
        )
        source_id = created.json()["id"]
        await stage_checkout(settings, source_id, slug="staged_plugin")
        response = await client.post(
            f"/api/v1/owner/marketplace/sources/{source_id}/import", headers=headers(owner)
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["package"]["slug"] == "staged_plugin"
        assert body["package"]["state"] in ("review_pending", "blocked")
        assert body["version"]["approval"] == "pending"
        assert body["inspection"]["execution_attempted"] is False
        assert body["inspection"]["licence"] == "MIT"

    async def test_blocked_package_cannot_be_approved(self, client, settings):
        owner = await make_owner(client, 5005)
        created = await client.post(
            "/api/v1/owner/marketplace/sources?url=https://github.com/o/r&ref=main",
            headers=headers(owner),
        )
        source_id = created.json()["id"]
        await stage_checkout(
            settings, source_id, slug="gpl_plugin", licence=GPL_TEXT
        )
        await client.post(
            f"/api/v1/owner/marketplace/sources/{source_id}/import", headers=headers(owner)
        )
        response = await client.post(
            "/api/v1/owner/marketplace/packages/gpl_plugin/approve", headers=headers(owner)
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "inspection_blocked"

    async def test_approve_then_install_gate(self, client, settings):
        """A marketplace package is not installable until an owner approves it."""
        from sqlalchemy import select

        from meiko.db.base import session_scope
        from meiko.db.models import Plugin

        owner = await make_owner(client, 5006)
        created = await client.post(
            "/api/v1/owner/marketplace/sources?url=https://github.com/o/r&ref=main",
            headers=headers(owner),
        )
        source_id = created.json()["id"]
        await stage_checkout(settings, source_id, slug="gated_plugin")
        await add_catalogue_row("gated_plugin")
        await client.post(
            f"/api/v1/owner/marketplace/sources/{source_id}/import", headers=headers(owner)
        )
        # Register the catalogue row as a marketplace plugin (no builtin runtime).
        await add_catalogue_row("gated_plugin")
        token, _ = await login(client, 5007)
        bot_id = (
            await client.post(
                "/api/v1/bots",
                json={"token": "123456789:" + "Z" * 34, "name": "B"},
                headers=headers(token),
            )
        ).json()["id"]
        before = await client.post(
            "/api/v1/plugins/gated_plugin/install",
            json={"bot_id": bot_id},
            headers=headers(token),
        )
        assert before.status_code == 400, before.text
        assert "approved" in before.json()["detail"]["message"]

        approved = await client.post(
            "/api/v1/owner/marketplace/packages/gated_plugin/approve", headers=headers(owner)
        )
        if approved.status_code != 200:
            detail = (
                await client.get(
                    "/api/v1/owner/marketplace/packages/gated_plugin", headers=headers(owner)
                )
            ).json()
            raise AssertionError(detail["versions"][0]["inspection"])
        assert approved.status_code == 200, approved.text
        assert approved.json()["approval"] == "approved"
        assert approved.json()["published"] is True

        after = await client.post(
            "/api/v1/plugins/gated_plugin/install",
            json={"bot_id": bot_id},
            headers=headers(token),
        )
        assert after.status_code == 201, after.text
        assert after.json()["state"] == "installed"

    async def test_reject_blocks_installation(self, client, settings):
        from sqlalchemy import select

        from meiko.db.base import session_scope
        from meiko.db.models import Plugin

        owner = await make_owner(client, 5008)
        created = await client.post(
            "/api/v1/owner/marketplace/sources?url=https://github.com/o/r&ref=main",
            headers=headers(owner),
        )
        source_id = created.json()["id"]
        await stage_checkout(settings, source_id, slug="rejected_plugin")
        await client.post(
            f"/api/v1/owner/marketplace/sources/{source_id}/import", headers=headers(owner)
        )
        rejected = await client.post(
            "/api/v1/owner/marketplace/packages/rejected_plugin/reject?reason=copied%20code",
            headers=headers(owner),
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["approval"] == "rejected"
        await add_catalogue_row("rejected_plugin")
        token, _ = await login(client, 5009)
        bot_id = (
            await client.post(
                "/api/v1/bots",
                json={"token": "123456789:" + "Z" * 34, "name": "B"},
                headers=headers(token),
            )
        ).json()["id"]
        response = await client.post(
            "/api/v1/plugins/rejected_plugin/install",
            json={"bot_id": bot_id},
            headers=headers(token),
        )
        assert response.status_code == 400

    async def test_package_detail_lists_versions_and_findings(self, client, settings):
        owner = await make_owner(client, 5010)
        created = await client.post(
            "/api/v1/owner/marketplace/sources?url=https://github.com/o/r&ref=main",
            headers=headers(owner),
        )
        source_id = created.json()["id"]
        await stage_checkout(
            settings,
            source_id,
            slug="detail_plugin",
            extra_files={"plugin.py": "import os\nos.system('id')\n"},
        )
        await client.post(
            f"/api/v1/owner/marketplace/sources/{source_id}/import", headers=headers(owner)
        )
        detail = await client.get(
            "/api/v1/owner/marketplace/packages/detail_plugin", headers=headers(owner)
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["state"] == "blocked"
        assert body["installable"] is False
        codes = {f["code"] for f in body["versions"][0]["inspection"]["findings"]}
        assert "scan.rce" in codes

    async def test_marketplace_overview_is_owner_only_and_counts(self, client, settings):
        token, _ = await login(client, 5011)
        assert (
            await client.get("/api/v1/owner/marketplace/overview", headers=headers(token))
        ).status_code == 403
        owner = await make_owner(client, 5012)
        created = await client.post(
            "/api/v1/owner/marketplace/sources?url=https://github.com/o/r&ref=main",
            headers=headers(owner),
        )
        source_id = created.json()["id"]
        await stage_checkout(settings, source_id, slug="counted_plugin")
        await client.post(
            f"/api/v1/owner/marketplace/sources/{source_id}/import", headers=headers(owner)
        )
        overview = (
            await client.get("/api/v1/owner/marketplace/overview", headers=headers(owner))
        ).json()
        assert overview["summary"]["sources"] == 1
        assert overview["summary"]["packages"] == 1
        assert overview["summary"]["pending_review"] == 1
        assert overview["packages"][0]["slug"] == "counted_plugin"
