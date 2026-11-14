"""Safe plugin import: inspect a repository, never run it.

The dangerous part of a plugin marketplace is not the install button, it is the
moment somebody else's repository is processed by your server.  A naive
implementation clones the repo, ``pip install``s it and calls ``setup.py`` – which
is remote code execution as a service.  This module is the opposite: it looks at
files, parses one manifest, and records what it saw.

The pipeline, in order (each step can fail closed):

1. **URL validation** – ``meiko.modules.urls`` (http/https, no credentials, no
   private hosts, no raw IP literals).  Importing ``file:///etc`` or a cloud
   metadata endpoint is not a plugin source.
2. **Pinned reference** – a branch is resolved to a commit *once*, and the commit
   is stored on the version row.  The importer never re-resolves "main".
3. **Repo inspection** – a static file listing.  No ``setup.py``, no
   ``npm install``, no shell hook, no import of anything from the repo.
4. **Licence check** – a licence file must exist and be on the allowlist; the
   owner can record an override, which is audited.
5. **Manifest validation** – the typed manifest from ``meiko.plugins.manifest``,
   including permissions, dependencies, resources and compatibility.
6. **Dependency + static security scan** – declared dependencies must be in the
   platform allowlist; the source is scanned for the patterns that make a plugin
   dangerous (subprocess, eval, network to private hosts, ctypes, ...).
7. **Owner review** – approval is a separate row and a separate action from
   execution state.  Nothing reaches a bot because somebody clicked install.

Everything here returns *findings*, which the owner UI displays.  The importer
does not decide, the owner does.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from meiko.logging_setup import get_logger
from meiko.modules import urls as url_policy
from meiko.plugins.manifest import MANIFEST_NAMES, ManifestError, PluginManifest

log = get_logger("plugins.marketplace")

#: Licences an imported package may carry.  Copyleft licences are absent on
#: purpose: importing GPL code into this codebase would relicense it.
APPROVED_LICENCES = {
    "mit": "MIT",
    "apache-2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "isc": "ISC",
    "unlicense": "Unlicense",
    "mpl-2.0": "MPL-2.0",
}

#: Third-party packages a plugin may declare.  Anything else needs an owner
#: decision, because "just add a dependency" is how a supply-chain attack is
#: introduced.
DEPENDENCY_ALLOWLIST = {
    "httpx",
    "aiofiles",
    "pillow",
    "pydub",
    "mutagen",
    "python-dateutil",
    "beautifulsoup4",
    "lxml",
    "pyyaml",
    "orjson",
    "tenacity",
    "cryptography",
    "yt-dlp",
}

#: Patterns that make a plugin unfit to run inside the platform process.  Each
#: one is a *finding*, not an automatic reject, so the owner sees the reason.
DANGEROUS_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("rce", r"\bos\.system\s*\(", "shell execution via os.system"),
    ("rce", r"\bsubprocess\.(?:run|call|Popen|check_output)\s*\(", "spawns a subprocess"),
    ("rce", r"\bos\.(?:execv|execve|spawnv)\w*\s*\(", "replaces the process image"),
    ("rce", r"\bpty\.spawn\s*\(", "spawns a pty"),
    ("eval", r"\beval\s*\(", "eval of arbitrary code"),
    ("eval", r"\bexec\s*\(", "exec of arbitrary code"),
    ("eval", r"__import__\s*\(", "dynamic import"),
    ("native", r"\bctypes\b", "loads native code"),
    ("native", r"pickle\.loads\s*\(", "deserialises pickle data"),
    ("network", r"requests\.(?:get|post)\s*\(", "direct outbound HTTP (use the platform client)"),
    ("secrets", r"(?:password|api[_-]?key|token)\s*=\s*['\"][^'\"]{8,}['\"]", "hard-coded credential"),
    ("filesystem", r"open\s*\(\s*['\"]/(?:etc|proc|sys)", "reads system paths"),
    ("obfuscation", r"base64\.b64decode\s*\(", "decodes an obfuscated payload"),
    ("obfuscation", r"(?:\\x[0-9a-fA-F]{2}){8,}", "long escaped byte string (obfuscation)"),
    # A plain ``open(...).write(...)`` at import time is indistinguishable from a
    # file dropper, and an install hook is exactly where it would hide.
    ("rce", r"open\s*\([^)\n]{0,80}\bw[a-z]?\b", "writes a file as part of the scanned source"),
    # A scan that only reads a package's ``.py`` files would miss a JS payload.
    ("eval", r"\bnew\s+Function\s*\(", "eval of arbitrary code (new Function)"),
    ("eval", r"\bchild_process\b", "spawns a subprocess"),
)

#: Files that a package may never need, and whose presence is a red flag.
SUSPICIOUS_FILES = {
    "setup.py": "runs code at install time; the platform installs from a manifest",
    "setup.cfg": "packaging metadata is taken from the manifest",
    "pyproject.toml": "packaging metadata is taken from the manifest",
    "makefile": "build hooks are not executed by the platform",
    "dockerfile": "the platform supplies the container",
}

#: Extensions allowed in a plugin package.  Binaries are refused: the platform
#: does not ship opaque blobs it cannot inspect.
ALLOWED_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".md", ".txt", ".html", ".css", ".js", ".svg", ".png", ".jpg"}

MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 400


@dataclass
class Finding:
    """One inspection result, in the vocabulary the owner UI shows."""

    severity: str  # info | warning | error | critical
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


@dataclass
class Inspection:
    """Everything the pipeline learned, plus the verdict it implies."""

    ok: bool = False
    commit: str = ""
    licence: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    execution_attempted: bool = False

    def add(self, severity: str, code: str, message: str, path: str = "") -> None:
        self.findings.append(Finding(severity, code, message, path))

    @property
    def blocked(self) -> bool:
        return any(f.severity in ("error", "critical") for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok and not self.blocked,
            "commit": self.commit,
            "licence": self.licence,
            "manifest": self.manifest,
            "files": self.files,
            "findings": [f.to_dict() for f in self.findings],
            "dependencies": self.dependencies,
            "permissions": self.permissions,
            # Recorded explicitly so the docs and the UI can state it: the
            # importer never runs the imported code.
            "execution_attempted": self.execution_attempted,
        }


# --------------------------------------------------------------------------- #
# 1. URL / reference validation
# --------------------------------------------------------------------------- #
def validate_source_url(raw: str) -> str:
    """Accept only a public http(s) git URL. Raises ``ValueError`` otherwise."""
    candidate = (raw or "").strip()
    if not candidate:
        raise ValueError("a source URL is required")
    if candidate.endswith(".git"):
        pass
    try:
        url_policy.check(candidate)
    except url_policy.UrlRejected as exc:
        raise ValueError(f"refused source URL: {exc}") from exc
    if "github.com" not in candidate and "gitlab.com" not in candidate and "codeberg.org" not in candidate:
        raise ValueError(
            "only well-known git hosts are accepted for import "
            "(github.com, gitlab.com, codeberg.org)"
        )
    return candidate


def normalise_ref(ref: str) -> str:
    """Reject refs that could be read as options or paths."""
    candidate = (ref or "").strip() or "main"
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,120}", candidate):
        raise ValueError("invalid git ref")
    if ".." in candidate or candidate.startswith("-"):
        raise ValueError("invalid git ref")
    return candidate


def resolve_commit(local_repo: Path, ref: str) -> str:
    """Resolve ``ref`` to a commit sha *inside an existing clone*.

    Read-only: ``rev-parse`` runs on a repository the platform already has.  It
    is not used to fetch or execute imported code.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "-C", str(local_repo), "rev-parse", f"{normalise_ref(ref)}^{{commit}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env dependent
        raise ValueError(f"could not resolve ref: {exc}") from exc
    sha = (completed.stdout or "").strip()
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError(f"could not resolve ref {ref!r} to a commit")
    return sha


# --------------------------------------------------------------------------- #
# 2. Static repository inspection
# --------------------------------------------------------------------------- #
def list_files(root: Path, *, limit: int = MAX_FILES) -> list[str]:
    """A static file listing. Reading names, not running anything."""
    if not root.is_dir():
        return []
    files: list[str] = []
    for path in sorted(root.rglob("*")):
        if len(files) >= limit:
            break
        if path.is_dir() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        files.append(path.relative_to(root).as_posix())
    return files


def find_manifest(root: Path) -> tuple[Path | None, str]:
    """Locate the manifest. Returns ``(path, reason_when_missing)``."""
    for name in MANIFEST_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate, ""
    # Shallower matches win, so "<repo>/<plugin>/meiko_plugin.json" is found while
    # a stray copy in "tests/fixtures/" never shadows the real one.
    for name in MANIFEST_NAMES:
        matches = [
            path
            for path in root.rglob(name)
            if path.is_file()
            and ".git" not in path.parts
            and "__pycache__" not in path.parts
        ]
        if not matches:
            continue
        matches.sort(key=lambda path: (len(path.relative_to(root).parts), str(path)))
        shallowest = len(matches[0].relative_to(root).parts)
        same_level = [m for m in matches if len(m.relative_to(root).parts) == shallowest]
        if len(same_level) > 1:
            names = ", ".join(m.relative_to(root).as_posix() for m in same_level)
            return None, f"several manifests at the same depth ({names}); import one path"
        return matches[0], ""
    return None, "no manifest (meiko_plugin.json) found in the package"


def detect_licence(root: Path) -> tuple[str, str]:
    """Return ``(spdx_id, raw_text)`` from a licence file, without guessing hard."""
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "LICENCE"):
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable file
            continue
        head = text[:2000].lower()
        for needle, spdx in (
            ("mit license", "MIT"),
            ("apache license", "Apache-2.0"),
            ("bsd 3-clause", "BSD-3-Clause"),
            ("bsd 2-clause", "BSD-2-Clause"),
            ("isc license", "ISC"),
            ("mozilla public license", "MPL-2.0"),
            ("unlicense", "Unlicense"),
            ("gnu general public license", "GPL"),
            ("gnu affero general public license", "AGPL"),
        ):
            if needle in head:
                return spdx, text[:200]
        return "unknown", text[:200]
    return "", ""


def scan_source(text: str, path: str) -> list[Finding]:
    """Static pattern scan. No import, no exec, no network."""
    findings: list[Finding] = []
    for code, pattern, explanation in DANGEROUS_PATTERNS:
        if re.search(pattern, text):
            severity = "critical" if code in ("rce", "eval", "obfuscation") else "warning"
            findings.append(Finding(severity, f"scan.{code}", explanation, path))
    return findings


def inspect_directory(
    root: Path,
    *,
    commit: str = "",
    owner_licence_override: bool = False,
    platform_version: str = "1.0.0",
) -> Inspection:
    """Run the whole static pipeline over an already-materialised directory.

    The caller materialises the repository (clone into a temp dir); this function
    only reads it.  Every early return records why it stopped, so the owner sees
    "refused: GPL licence" rather than a blank screen.
    """
    result = Inspection(commit=commit)
    files = list_files(root)
    result.files = files
    if not files:
        result.add("error", "repo.empty", "the repository contains no files")
        return result
    if len(files) >= MAX_FILES:
        result.add("error", "repo.too_large", f"more than {MAX_FILES} files; split the package")
        return result

    for name in files:
        base = name.rsplit("/", 1)[-1].lower()
        if base in SUSPICIOUS_FILES:
            result.add(
                "warning",
                "file.install_hook",
                f"{base} would be an install-time hook; it is never executed ({SUSPICIOUS_FILES[base]})",
                name,
            )
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if suffix and suffix not in ALLOWED_SUFFIXES and not name.lower().startswith("license"):
            result.add("warning", "file.unexpected_type", f"unexpected file type {suffix}", name)

    # Static scan -------------------------------------------------------------
    # This used to run *after* the licence and manifest gates returned early, so a
    # package that shipped a hook and no manifest was reported as "manifest.missing"
    # alone and its payload was never scanned. Scanning is the security control; it
    # must not depend on the package being otherwise well-formed.
    scanned = 0
    for name in files:
        if not name.endswith((".py", ".js", ".mjs", ".cjs", ".sh", ".json", ".yaml", ".yml")):
            continue
        path = root / name
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                result.add("warning", "file.too_large", "file skipped by the scanner (too large)", name)
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable file
            result.add("warning", "file.unreadable", "file could not be read", name)
            continue
        scanned += 1
        result.findings.extend(scan_source(text, name))
    result.add("info", "scan.completed", f"statically scanned {scanned} files; nothing was executed")

    # Licence -----------------------------------------------------------------
    spdx, _text = detect_licence(root)
    result.licence = spdx
    if not spdx:
        result.add("error", "licence.missing", "no licence file; the owner must record one")
    elif spdx not in ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "Unlicense"):
        severity = "warning" if owner_licence_override else "error"
        result.add(
            severity,
            "licence.incompatible",
            f"{spdx} is not on the import allowlist; an owner override is required and audited",
        )

    # Manifest ----------------------------------------------------------------
    manifest_path, reason = find_manifest(root)
    if manifest_path is None:
        result.add("error", "manifest.missing", reason)
        return result
    try:
        manifest = PluginManifest.from_file(manifest_path)
    except (ManifestError, ValueError, TypeError) as exc:
        result.add("error", "manifest.invalid", str(exc), manifest_path.name)
        return result
    try:
        manifest.validate(platform_version=platform_version)
    except ManifestError as exc:
        result.add("error", "manifest.incompatible", str(exc), manifest_path.name)
    for problem in manifest.validate_config_schema():
        result.add("warning", "manifest.config_schema", problem, manifest_path.name)
    result.manifest = manifest.to_dict()
    result.permissions = list(manifest.permissions)
    result.dependencies = list(manifest.dependencies)

    # Dependencies ------------------------------------------------------------
    for dependency in manifest.dependencies:
        if dependency not in DEPENDENCY_ALLOWLIST:
            result.add(
                "error",
                "dependency.not_allowlisted",
                f"dependency {dependency!r} is not on the platform allowlist",
            )
    for dependency in getattr(manifest, "platform_dependencies", []) or []:
        result.add("warning", "dependency.unchecked", str(dependency))

    result.ok = not result.blocked
    return result


def required_dependencies_missing(declared: Iterable[str], available: Iterable[str]) -> list[str]:
    """Manifest dependencies that no installed plugin provides."""
    have = set(available)
    return [slug for slug in declared if slug not in have]
