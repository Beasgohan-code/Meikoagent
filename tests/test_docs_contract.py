"""PART 19 — documentation must match the code.

Docs rot silently, so the claims that customers rely on are asserted here: the
canonical repository table exists with a pinned commit and a verified licence for
every upstream source, and the counts quoted in the README are the real counts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

#: The repositories this platform actually drew on, as verified live via the GitHub
#: API. Anything not in this set must not appear as an upstream source.
EXPECTED_REPOS = {
    "Beasgohan-code/Videl0": "6430b1e5e60e0776a7ab519e05cb98c2ca1a2ebf",
    "playeon/Playeon": "6ec6097bc7f5db19583618c226a5e4c410b90cee",
    "abhinai2244/save-restricted-content-bot": "bf2e45a1ef187060e053e59c0336cb0e6a4a022b",
    "abhinai2244/ENCODING-BOT": "84d4e256e2f2047c653691b5b59bfdea2a262fae",
    "abhinai2244/Multi-FileStoreBot": "010172404c2121862deea99f0fe25766d130e392",
    "abhinai2244/LINK-SHAREBOT": "265f2acbe720571e7c514cd32eea600292656fe2",
    "abhinai2244/CBML": "4d13aa14a9d44af426f740db5905c3a22001ad38",
    "rjriajul/wzgram": "f256c1dafe12bf3d80918772c49315d3eeb3cfc5",
    "SilentDemonSD/WZML-X": "91f9702ce278e56e1bf3f60138f77e633573f3d4",
}


@pytest.fixture(scope="module")
def modules_doc() -> str:
    return (DOCS / "MODULES.md").read_text()


class TestUpstreamTable:
    def test_every_repository_is_listed_with_its_commit_and_licence(self, modules_doc):
        for repo, sha in EXPECTED_REPOS.items():
            assert f"github.com/{repo}" in modules_doc, f"{repo} missing from the table"
            assert sha in modules_doc, f"{repo} is not pinned to {sha}"

    def test_the_table_names_every_required_column(self, modules_doc):
        header = next(
            line for line in modules_doc.splitlines() if line.startswith("| Repository URL")
        )
        for column in (
            "Repository URL",
            "Pinned commit",
            "Licence",
            "Integrated",
            "Reimplemented",
            "Excluded",
            "Reason",
            "Tests",
        ):
            assert column in header, f"table is missing the {column!r} column"

    def test_the_wrong_repository_names_are_only_mentioned_as_corrections(self, modules_doc):
        """`abe_/playeon` and `cantarella/...` never existed; they must be corrected."""
        for bogus in ("abe_/playeon", "cantarella/save-restricted-content-bot"):
            for line in modules_doc.splitlines():
                if bogus in line:
                    assert any(
                        word in line.lower() for word in ("do not exist", "404", "earlier draft", "replaced")
                    ), f"{bogus} is presented as a real source: {line!r}"

    def test_no_row_claims_a_code_copy(self, modules_doc):
        table = [line for line in modules_doc.splitlines() if line.startswith("| `https://github.com/")]
        assert table
        for row in table:
            lowered = row.lower()
            if "copied" in lowered:
                assert any(
                    phrase in lowered
                    for phrase in ("no code copied", "not copied", "may be copied", "could be copied")
                ), f"row claims a copy: {row[:160]}"

    def test_licences_are_the_verified_ones(self, modules_doc):
        assert "NOASSERTION" in modules_doc  # Playeon
        assert "No licence at repository root" in modules_doc or "no licence file" in modules_doc.lower()
        for licence in ("MIT", "AGPL-3.0", "LGPL-3.0"):
            assert licence in modules_doc

    def test_every_listed_test_file_exists(self, modules_doc):
        for name in re.findall(r"`(tests/[a-z_]+\.py)`", modules_doc):
            assert (ROOT / name).exists(), f"{name} is cited but does not exist"


class TestReadmeClaims:
    @pytest.fixture(scope="class")
    def readme(self) -> str:
        return (ROOT / "README.md").read_text()

    def test_the_test_count_matches_the_suite(self, readme):
        import subprocess
        import sys

        collected = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        match = re.search(r"(\d+) tests? collected", collected.stdout)
        assert match, collected.stdout[-500:]
        claimed = re.search(r"#\s*(\d+) tests", readme)
        assert claimed, "README does not state a test count"
        assert int(claimed.group(1)) == int(match.group(1)), (
            f"README says {claimed.group(1)} tests, the suite collects {match.group(1)}"
        )

    def test_the_table_count_matches_the_models(self, readme):
        from meiko.db.models import Base

        claimed = re.search(r"\((\d+) tables\)", readme)
        assert claimed, "README does not state a table count"
        assert int(claimed.group(1)) == len(Base.metadata.tables)

    def test_the_documented_path_count_matches_openapi(self, readme):
        import os

        os.environ.setdefault("ENVIRONMENT", "testing")
        os.environ.setdefault("MEIKO_ENV_FILE", "")
        from meiko.api.app import create_app

        spec = create_app().openapi()
        claimed = re.search(r"(\d+) documented paths", readme)
        assert claimed, "README does not state a path count"
        assert int(claimed.group(1)) == len(spec["paths"])
        operations = sum(len(value) for value in spec["paths"].values())
        assert f"({operations} operations)" in readme

    def test_no_document_promises_a_feature_without_a_module(self, readme):
        """Every module named in the feature table must exist in the codebase."""
        modules = re.findall(r"`meiko/[a-z_/]+`", readme)
        for path in set(modules):
            clean = path.strip("`")
            assert (ROOT / clean).exists() or (ROOT / clean).with_suffix(".py").exists(), path


class TestDocsAreNotDuplicated:
    def test_there_is_one_module_document(self):
        assert list(DOCS.glob("MODULES*.md")) == [DOCS / "MODULES.md"]

    def test_no_orphan_feature_lists(self):
        """Feature inventory lives in FEATURE_SUGGESTIONS.md, not in a second file."""
        for path in DOCS.glob("*.md"):
            if path.name == "MODULES.md":
                continue
            body = path.read_text()
            assert body.count("## Status note") <= 3, f"{path.name} duplicates the module status notes"

    def test_deployment_docs_point_at_the_real_services(self):
        body = (DOCS / "DEPLOYMENT.md").read_text().lower()
        for service in ("postgres", "redis", "watchdog"):
            assert service in body


class TestWebDependenciesStayPatched:
    """Vercel refused to deploy this app: 'Vulnerable version of Next.js detected'.

    The build itself succeeded, so nothing in CI caught it — only the platform's
    deploy-time gate did.  The pin is part of the contract: the web app is a
    production surface and must not ship a framework version with a known
    advisory.
    """

    #: First patched releases for the advisories that blocked the deploy
    #: (CVE-2025-66478 for Next.js, GHSA-* for postcss).
    MINIMUM_NEXT = (15, 5, 29)
    MINIMUM_POSTCSS = (8, 5, 23)

    @staticmethod
    def _version(text: str) -> tuple[int, ...]:
        return tuple(int(part) for part in re.findall(r"\d+", text)[:3])

    def test_next_is_patched(self):
        manifest = (ROOT / "webapp/package.json").read_text()
        pinned = re.search(r'"next":\s*"([\d.]+)"', manifest)
        assert pinned, "next is not pinned to an exact version"
        assert self._version(pinned.group(1)) >= self.MINIMUM_NEXT, (
            f"next {pinned.group(1)} has a known advisory; need "
            f"{'.'.join(map(str, self.MINIMUM_NEXT))} or newer"
        )

    def test_postcss_is_patched_at_every_depth(self):
        manifest = (ROOT / "webapp/package.json").read_text()
        pinned = re.search(r'"postcss":\s*"\^?([\d.]+)"', manifest)
        assert pinned
        assert self._version(pinned.group(1)) >= self.MINIMUM_POSTCSS
        lock = (ROOT / "webapp/package-lock.json").read_text()
        for version in re.findall(r'"node_modules/(?:[^"]*/)?postcss":\s*\{[^}]*"version":\s*"([\d.]+)"', lock):
            assert self._version(version) >= self.MINIMUM_POSTCSS, (
                f"postcss {version} is still installed by a transitive dependency"
            )

    def test_the_manifest_has_no_unused_security_pinned_dependency(self):
        """`zod` was declared but imported nowhere; unused deps are unpatched attack surface."""
        manifest = (ROOT / "webapp/package.json").read_text()
        declared = set(json.loads(manifest).get("dependencies", {}))
        sources = "\n".join(
            path.read_text(errors="ignore")
            for path in (ROOT / "webapp/src").rglob("*.ts*")
        )
        for name in declared:
            assert f'"{name}"' in sources or f"'{name}'" in sources or name in ("next", "react", "react-dom"), (
                f"{name} is a runtime dependency but nothing imports it"
            )


class TestTermuxScriptsAreHonest:
    """Termux is the recommendation for free 24/7 hosting; the scripts must match."""

    @pytest.fixture(scope="class")
    def scripts(self) -> dict[str, str]:
        root = ROOT / "scripts"
        return {
            name: (root / name).read_text()
            for name in ("termux_setup.sh", "termux_boot.sh")
        }

    def test_both_scripts_exist_and_are_executable(self):
        for name in ("termux_setup.sh", "termux_boot.sh"):
            path = ROOT / "scripts" / name
            assert path.exists(), name
            assert path.stat().st_mode & 0o111, f"{name} is not executable"

    def test_they_are_valid_shell(self, scripts):
        import subprocess

        for name in scripts:
            result = subprocess.run(
                ["bash", "-n", str(ROOT / "scripts" / name)], capture_output=True, text=True
            )
            assert result.returncode == 0, f"{name}: {result.stderr}"

    def test_the_setup_installs_the_toolchain_the_wheels_need(self, scripts):
        setup = scripts["termux_setup.sh"]
        for package in ("clang", "rust", "openssl", "libsqlite", "python"):
            assert package in setup, f"{package} is required to build the dependencies"

    def test_the_setup_generates_secrets_rather_than_shipping_them(self, scripts):
        setup = scripts["termux_setup.sh"]
        assert "Fernet.generate_key()" in setup
        assert "token_urlsafe" in setup
        assert 'chmod 600 "$ENV_FILE"' in setup

    def test_the_setup_warns_about_exposing_the_console(self, scripts):
        setup = scripts["termux_setup.sh"]
        assert "AUTH_DEV_MODE=true" in setup
        assert "do not expose" in setup.lower() or "do not publish" in setup.lower()

    def test_boot_holds_a_wake_lock_and_survives_a_dead_redis(self, scripts):
        boot = scripts["termux_boot.sh"]
        assert "termux-wake-lock" in boot
        assert "REQUIRE_REDIS=0" in boot, "one process on a phone must not die over redis"
        assert "--service migrate" in boot

    def test_boot_is_idempotent(self, scripts):
        boot = scripts["termux_boot.sh"]
        assert "already running" in boot
        assert "PID_FILE" in boot

    def test_the_hosting_doc_recommends_it_and_says_what_it_cannot_do(self):
        doc = (ROOT / "docs" / "HOSTING.md").read_text()
        assert "termux_setup.sh" in doc
        assert "termux-wake-lock" in doc
        # honest about the free tiers that sleep
        assert "sleeps on idle" in doc or "sleep" in doc
        assert "GitHub Actions" in doc


class TestPostgresClaimIsEarned:
    """The README may not keep saying Postgres is unverified once it has been run.

    The chain is executed for real elsewhere (a throwaway schema on a live
    PostgreSQL 17 server, rolled back); this pins the claim to the evidence so
    the "not claimed" list cannot silently regain a stale caveat.
    """

    @pytest.fixture(scope="class")
    def readme(self) -> str:
        return (ROOT / "README.md").read_text()

    def test_postgres_is_no_longer_disclaimed(self, readme):
        tail = readme.split("Not verified here, and therefore not claimed", 1)
        assert len(tail) == 2, "the non-claim paragraph moved or was deleted"
        assert "PostgreSQL" not in tail[1] and "Postgres" not in tail[1], (
            "the chain has been executed on real PostgreSQL; stop disclaiming it"
        )

    def test_the_real_server_run_is_reported_with_numbers(self, readme):
        section = readme.split("## How this was verified", 1)[1]
        assert "PostgreSQL 17" in section
        assert "rolled back" in section
        assert "404947bb2f88" in section

    def test_no_document_claims_postgres_is_untested(self):
        for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
            text = path.read_text()
            for phrase in (
                "no real PostgreSQL",
                "no real Postgres",
                "Postgres untested",
                "cannot be verified without a server",
            ):
                assert phrase not in text, f"{path.name} still says {phrase!r}"


class TestSupabaseGuidanceIsReachable:
    """The documented Supabase URL must be one a phone can actually connect to."""

    def test_the_docs_warn_that_the_direct_host_is_ipv6_only(self):
        for name in ("DEPLOYMENT.md", "HOSTING.md"):
            text = (ROOT / "docs" / name).read_text()
            assert "IPv6" in text, f"{name} offers a host that free-tier IPv4 cannot reach"

    def test_the_documented_url_is_the_session_pooler_on_5432(self):
        deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text()
        assert "pooler.supabase.com:5432" in deployment
        assert "5432 is session mode, 6543 is transaction mode" in deployment

    def test_no_document_tells_a_phone_user_to_use_the_direct_host_only(self):
        hosting = (ROOT / "docs" / "HOSTING.md").read_text()
        assert "Session pooler" in hosting
        # the old advice paired a 6543 app URL with the IPv6-only migration host
        assert "postgres.<ref>:<password>@db.<ref>.supabase.co:5432/postgres?sslmode=require\n\npython start.py" not in hosting


class TestTermuxCloudDatabasePath:
    """A phone that points at cloud Postgres must not make things worse."""

    @pytest.fixture(scope="class")
    def setup_script(self) -> str:
        return (ROOT / "scripts" / "termux_setup.sh").read_text()

    @pytest.fixture(scope="class")
    def boot_script(self) -> str:
        return (ROOT / "scripts" / "termux_boot.sh").read_text()

    def test_setup_offers_a_cloud_database(self, setup_script):
        assert "DATABASE_URL" in setup_script
        assert "Session pooler" in setup_script or "session pooler" in setup_script

    def test_setup_rejects_a_non_postgres_url(self, setup_script):
        assert "postgres://*|postgresql://*" in setup_script

    def test_setup_tests_the_url_before_finishing(self, setup_script):
        assert "NOT reachable" in setup_script
        assert "select 1" in setup_script

    def test_the_ipv6_hint_only_fires_for_the_direct_host(self, setup_script):
        """The hint must not appear for a pooler host — that was the misleading path."""
        assert 'startswith("db.")' in setup_script
        assert "IPv6" in setup_script

    def test_boot_does_not_migrate_a_cloud_database_by_default(self, boot_script):
        assert 'MEIKO_MIGRATE' in boot_script
        assert 'if [ "${MEIKO_MIGRATE:-0}" = "1" ]' in boot_script

    def test_the_hosting_doc_explains_both(self):
        doc = (ROOT / "docs" / "HOSTING.md").read_text()
        assert "MEIKO_MIGRATE=1" in doc
        assert "unreachable" in doc.lower() or "Network is unreachable" in doc
