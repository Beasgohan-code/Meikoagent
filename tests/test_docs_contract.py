"""PART 19 — documentation must match the code.

Docs rot silently, so the claims that customers rely on are asserted here: the
canonical repository table exists with a pinned commit and a verified licence for
every upstream source, and the counts quoted in the README are the real counts.
"""

from __future__ import annotations

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
