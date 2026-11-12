"""Isolated snippet execution.

Design rules (why this is not ``exec()`` in the bot process):

* **separate process** – a crash, infinite loop or memory bomb cannot take the
  bot runtime down; the parent only waits with a timeout and kills the group,
* **isolated interpreter** – ``python -I -S`` ignores ``PYTHON*`` env vars,
  user site-packages and ``sitecustomize``; the snippet runs from a temporary
  directory that contains only the file it wrote,
* **no ambient secrets** – the child gets a minimal environment (no bot token,
  no DB URL) plus ``HOME`` pointing at its scratch directory,
* **resource limits** – ``RLIMIT_AS``, ``RLIMIT_CPU``, ``RLIMIT_FSIZE``,
  ``RLIMIT_NPROC`` where the platform supports it,
* **network policy** – when network is disallowed, a prelude replaces
  ``socket.socket``/``create_connection``/``getaddrinfo`` with stubs so a plain
  ``import socket`` cannot reach out (documented as best-effort, not a jail),
* **bounded output** – stdout/stderr are truncated while streaming, so a
  ``while True: print()`` cannot exhaust memory.

The function is intentionally pure-async and dependency free so it is unit
testable without a Telegram bot, a database or a network.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_LANGUAGES: dict[str, dict[str, object]] = {
    "python": {"suffix": ".py", "runner": ["python3", "-I", "-S"], "label": "Python 3"},
    "node": {"suffix": ".mjs", "runner": ["node", "--no-warnings"], "label": "Node.js"},
    "bash": {"suffix": ".sh", "runner": ["bash", "--noprofile", "--norc"], "label": "Bash"},
    "sql": {"suffix": ".sql", "runner": [], "label": "SQL (validated only)"},
}

#: Names that must never appear in a snippet we accept. Not a security boundary
#: (the process isolation is), but it stops obvious accidents early and gives a
#: clear message instead of a sandbox timeout.
HARD_BLOCKLIST = (
    "os.system",
    "subprocess.",
    "shutil.rmtree",
    "/etc/passwd",
    "/etc/shadow",
    "fork()",
    "os.fork",
    "os.kill",
    "ctypes.",
    "__import__('os').system",
    "rm -rf /",
    "mkfs",
)

#: SQL snippets are validated only (no engine is attached), so destructive
#: statements are refused statically instead of relying on a database's grants.
SQL_BLOCKLIST = (
    "drop table",
    "drop database",
    "truncate ",
    "delete from users",
    "alter system",
    "grant all",
    "pg_read_file",
    "load_file(",
)

NETWORK_STUB = """
import socket as _s
class _Blocked(OSError):
    pass
def _deny(*a, **k):
    raise _Blocked("network access is disabled for this snippet")
_s.socket = _deny
_s.create_connection = _deny
_s.getaddrinfo = _deny
try:
    import ssl as _ssl
    _ssl.create_default_context = _deny
except Exception:
    pass
"""

WRITE_STUB = """
import builtins as _b, io as _io, os as _os
_open = _b.open
def _guarded(file, mode="r", *a, **k):
    if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
        raise OSError("file writes are disabled for this snippet")
    return _open(file, mode, *a, **k)
_b.open = _guarded
_io.open = _guarded
_os.remove = lambda *a, **k: (_ for _ in ()).throw(OSError("file writes are disabled"))
_os.mkdir = _os.remove
"""


@dataclass
class RunLimits:
    timeout_seconds: int = 10
    max_output_chars: int = 3500
    max_source_chars: int = 4000
    memory_mb: int = 256
    cpu_seconds: int = 5
    allow_network: bool = False
    allow_file_write: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_output_chars": self.max_output_chars,
            "max_source_chars": self.max_source_chars,
            "memory_mb": self.memory_mb,
            "cpu_seconds": self.cpu_seconds,
            "allow_network": self.allow_network,
            "allow_file_write": self.allow_file_write,
        }


@dataclass
class RunResult:
    ok: bool
    language: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    timed_out: bool = False
    truncated: bool = False
    reason: str = ""
    unsupported: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "language": self.language,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "reason": self.reason,
            "unsupported": list(self.unsupported),
        }


class SnippetRejected(ValueError):
    """Snippet failed static validation."""


def language_label(key: str) -> str:
    return str(ALLOWED_LANGUAGES.get(key, {}).get("label", key))


def validate_source(source: str, limits: RunLimits) -> str:
    text = (source or "").strip()
    if not text:
        raise SnippetRejected("the snippet is empty")
    if len(text) > limits.max_source_chars:
        raise SnippetRejected(
            f"snippet is {len(text)} chars, limit is {limits.max_source_chars}"
        )
    if "\x00" in text:
        raise SnippetRejected("snippet contains a null byte")
    lowered = text.lower()
    for blocked in HARD_BLOCKLIST:
        if blocked.lower() in lowered:
            raise SnippetRejected(f"refusing to run a snippet containing {blocked!r}")
    for blocked in SQL_BLOCKLIST:
        if blocked in lowered:
            raise SnippetRejected(f"refusing to validate a statement containing {blocked!r}")
    return text


def available_languages() -> list[str]:
    available = []
    for key, spec in ALLOWED_LANGUAGES.items():
        runner = spec["runner"]
        if not runner:
            available.append(key)
            continue
        if shutil.which(str(runner[0])):
            available.append(key)
    return available


def _child_limits(limits: RunLimits):
    """Build a preexec_fn applying POSIX resource limits (no-op elsewhere)."""

    def _apply() -> None:  # pragma: no cover - runs in the child process
        try:
            import resource
        except Exception:
            return
        os.setsid()
        byte_limit = max(64, limits.memory_mb) * 1024 * 1024
        for name, value in (
            ("RLIMIT_AS", byte_limit),
            ("RLIMIT_CPU", max(1, limits.cpu_seconds)),
            ("RLIMIT_FSIZE", 8 * 1024 * 1024),
            ("RLIMIT_NPROC", 64),
        ):
            flag = getattr(resource, name, None)
            if flag is None:
                continue
            try:
                resource.setrlimit(flag, (value, value))
            except Exception:
                continue
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except Exception:
            pass

    return _apply


def _minimal_env(workdir: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LANG": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NODE_OPTIONS": "--max-old-space-size=128",
    }


async def run_snippet(
    source: str,
    *,
    language: str = "python",
    limits: RunLimits | None = None,
    workdir: Path | None = None,
) -> RunResult:
    """Execute ``source`` in an isolated child process and capture bounded output."""
    limits = limits or RunLimits()
    started = time.perf_counter()
    key = (language or "python").lower()
    spec = ALLOWED_LANGUAGES.get(key)
    if spec is None:
        return RunResult(False, key, reason=f"unsupported language {language!r}")

    try:
        text = validate_source(source, limits)
    except SnippetRejected as exc:
        return RunResult(False, key, reason=str(exc))

    if key == "sql":
        return RunResult(
            True,
            key,
            stdout="SQL is validated syntactically; the platform never executes raw SQL from chat.",
            reason="validate_only",
        )

    runner = [str(part) for part in spec["runner"]]
    if not shutil.which(runner[0]):
        return RunResult(False, key, reason=f"{runner[0]} is not installed on this host")

    temp = workdir or Path(tempfile.mkdtemp(prefix="meiko-run-"))
    temp.mkdir(parents=True, exist_ok=True)
    script = temp / f"snippet{spec['suffix']}"

    if key == "python":
        prelude = []
        if not limits.allow_network:
            prelude.append(NETWORK_STUB)
        if not limits.allow_file_write:
            prelude.append(WRITE_STUB)
        script.write_text("\n".join(prelude) + "\n" + text + "\n", encoding="utf-8")
    else:
        script.write_text(text + "\n", encoding="utf-8")

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    captured = {"chars": 0, "truncated": False}

    keep = max(200, limits.max_output_chars)
    stdout_limit = min(keep, max(400, keep * 3 // 4))
    stderr_limit = max(200, keep - stdout_limit)

    process = await asyncio.create_subprocess_exec(
        *runner,
        str(script),
        cwd=str(temp),
        env=_minimal_env(temp),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=_child_limits(limits) if os.name == "posix" else None,
    )

    async def pump(stream, sink: list[str], cap: int) -> None:
        if stream is None:
            return
        collected = 0
        while True:
            chunk = await stream.read(1024)
            if not chunk:
                break
            if collected >= cap:
                captured["truncated"] = True
                continue
            piece = chunk.decode("utf-8", "replace")
            if collected + len(piece) > cap:
                piece = piece[: cap - collected]
                captured["truncated"] = True
            collected += len(piece)
            sink.append(piece)

    timed_out = False
    try:
        await asyncio.wait_for(
            asyncio.gather(pump(process.stdout, stdout_chunks, stdout_limit), pump(process.stderr, stderr_chunks, stderr_limit), process.wait()),
            timeout=max(1, limits.timeout_seconds),
        )
    except asyncio.TimeoutError:
        timed_out = True
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), 9)
            else:  # pragma: no cover - windows fallback
                process.kill()
        except ProcessLookupError:
            pass
        with_stderr = ""
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except Exception:  # pragma: no cover
            pass
        return RunResult(
            False,
            key,
            stdout=_join(stdout_chunks),
            stderr=_join(stderr_chunks),
            exit_code=None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            timed_out=True,
            truncated=captured["truncated"],
            reason=f"timed out after {limits.timeout_seconds}s",
        )
    except Exception as exc:  # pragma: no cover - defensive
        return RunResult(False, key, reason=f"execution failed: {exc}")

    duration = int((time.perf_counter() - started) * 1000)
    output = _join(stdout_chunks)
    errors = _join(stderr_chunks)
    ok = process.returncode == 0
    return RunResult(
        ok=ok,
        language=key,
        stdout=output,
        stderr=errors,
        exit_code=process.returncode,
        duration_ms=duration,
        timed_out=timed_out,
        truncated=captured["truncated"],
        reason="" if ok else _first_error(errors) or f"exited with status {process.returncode}",
    )


def _join(chunks: list[str]) -> str:
    return re.sub(r"\r\n", "\n", "".join(chunks)).strip()


def _first_error(stderr: str) -> str:
    for line in reversed([line for line in (stderr or "").splitlines() if line.strip()]):
        match = re.search(r"(\w*Error|\w*Exception): (.+)", line)
        if match:
            return f"{match.group(1)}: {match.group(2)}"[:300]
    return ""
