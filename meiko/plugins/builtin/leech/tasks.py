"""Mirror/leech task engine (CBML parity, without the vendored download stack).

CBML drives aria2/qBittorrent/SABnzbd/rclone with pyrogram uploads.  None of
that is installed here, and pretending otherwise is worse than useless — so the
engine models the *task lifecycle* faithfully and delegates the actual transfer
to a pluggable :class:`TransferBackend`:

* :class:`LocalBackend` — a real, dependency-free copier for local paths and
  ``http(s)`` URL downloads via ``httpx`` (what CI and this sandbox can run),
* :class:`YtDlpBackend`` — real site extraction (YouTube and ~1800 others) when
  the ``yt-dlp`` package is importable; reports ``available() == False`` and
  says how to install it otherwise,
* :class:`DaemonBackend` — aria2/qBittorrent/rclone hooks an operator can enable
  when those daemons exist; they report ``available() == False`` otherwise.

[`pick_backend`] chooses between them from the flags a user typed, so ``-aria2``
is never silently downgraded to an HTTP copy: either the daemon answers or the
task fails with the reason.

Everything a user sees (task ids, states, progress bar, ETA, cancel, queue
limits, per-user concurrency) is real state, not theatre.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol


class TaskState(str, Enum):
    QUEUED = "queued"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: CBML-style short codes people type as flags.
FLAG_ALIASES = {
    "leech": "leech",
    "l": "leech",
    "mirror": "mirror",
    "m": "mirror",
    "zip": "zip",
    "unzip": "unzip",
    "rclone": "rclone",
    "ytdl": "ytdl",
    "qbit": "qbit",
    "aria2": "aria2",
    "seed": "seed",
    "extract": "unzip",
}

CANCELLED = "cancelled"


class TransferError(RuntimeError):
    """The backend refused or failed a transfer."""


class TransferBackend(Protocol):
    name: str

    def available(self) -> bool:  # pragma: no cover - trivial
        ...

    async def fetch(self, url: str, destination: str, *, on_progress: Callable[[int, int], Awaitable[None]] | None = None) -> str:  # pragma: no cover
        ...


class LocalBackend:
    """Local copies + plain HTTP downloads. No daemons, no ffmpeg, no rclone."""

    name = "local"

    def available(self) -> bool:
        return True

    async def fetch(self, url: str, destination: str, *, on_progress=None) -> str:
        if url.startswith("file://"):
            url = url[7:]
        if not url.startswith(("http://", "https://")):
            if not os.path.exists(url):
                raise TransferError(f"{url} does not exist")
            size = os.path.getsize(url)
            with open(url, "rb") as source, open(destination, "wb") as target:
                copied = 0
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    copied += len(chunk)
                    if on_progress is not None:
                        await on_progress(copied, size)
            return destination

        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - httpx is a hard dep here
            raise TransferError("httpx is required for URL downloads") from exc
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or 0)
                done = 0
                with open(destination, "wb") as handle:
                    async for chunk in response.aiter_bytes(256 * 1024):
                        handle.write(chunk)
                        done += len(chunk)
                        if on_progress is not None:
                            await on_progress(done, total)
        return destination


class YtDlpBackend:
    """Site extraction through ``yt-dlp`` (install with ``pip install yt-dlp``)."""

    name = "ytdl"

    def __init__(
        self,
        *,
        max_bytes: int = 2000 * 1024 * 1024,
        binary: str = "",
        proxy: str = "",
        cookies: str = "",
        audio_only: bool = False,
    ) -> None:
        self.max_bytes = max_bytes
        self.binary = binary
        self.proxy = proxy.strip()
        self.cookies = cookies.strip()
        self.audio_only = audio_only

    @staticmethod
    def module():
        import importlib

        try:
            return importlib.import_module("yt_dlp")
        except Exception:  # optional dependency
            return None

    def available(self) -> bool:
        return self.module() is not None

    async def fetch(self, url: str, destination: str, *, on_progress=None) -> str:
        module = self.module()
        if module is None:
            raise TransferError("yt-dlp is not installed (pip install yt-dlp)")

        def report(status: dict[str, Any]) -> None:
            if on_progress is None or status.get("status") != "downloading":
                return
            done = int(status.get("downloaded_bytes") or 0)
            total = int(status.get("total_bytes") or status.get("total_bytes_estimate") or 0)
            if total > self.max_bytes:
                raise TransferError(f"the file is larger than the {self.max_bytes} byte limit")
            # the callback is async; schedule it without blocking the extractor
            asyncio.get_event_loop().create_task(on_progress(done, total))

        fmt = (
            "bestaudio[ext=m4a]/bestaudio/best"
            if self.audio_only
            else "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        )
        options: dict[str, Any] = {
            "format": fmt,
            "outtmpl": destination,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [report],
            "max_filesize": self.max_bytes,
            "restrictfilenames": True,
            "noplaylist": True,
        }
        if self.binary:
            options["ffmpeg_location"] = self.binary
        if self.proxy:
            options["proxy"] = self.proxy if "://" in self.proxy else f"http://{self.proxy}"
        if self.cookies and os.path.isfile(self.cookies):
            # a cookie jar is a credential: honoured when the owner points at one,
            # never fetched, copied or included in an error message
            options["cookiefile"] = self.cookies

        def download() -> str:
            with module.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                return str(ydl.prepare_filename(info)) if info else destination

        try:
            produced = await asyncio.to_thread(download)
        except Exception as exc:  # yt_dlp raises its own DownloadError
            raise TransferError(f"yt-dlp could not fetch this URL: {exc}") from exc
        if not os.path.exists(produced):
            # post-processing (merging) can change the extension
            produced = destination
        if not os.path.exists(produced):
            raise TransferError("yt-dlp reported success but no file was written")
        return produced


class DaemonBackend:
    """aria2 / qBittorrent / rclone, driven over their own RPC when reachable.

    CBML starts and authenticates those daemons itself; that is a lot of process
    management for a feature most deployments do not want, so here a daemon is
    something an operator runs and points at (``daemon_endpoint``). The call is
    made for real when the endpoint answers — including aria2's two-step
    ``aria2.addUri`` + ``aria2.tellStatus`` polling — and the failure names the
    endpoint when it does not. No path silently degrades to an HTTP copy.
    """

    RPC_METHODS = {"aria2": "aria2", "qbittorrent": "qbittorrent", "rclone": "rclone"}

    def __init__(self, name: str, endpoint: str = "", *, secret: str = "", timeout: float = 15.0) -> None:
        self.name = name
        self.endpoint = endpoint
        self.secret = secret
        self.timeout = timeout

    def available(self) -> bool:
        if not self.endpoint:
            return False
        import socket
        try:
            from urllib.parse import urlparse

            parsed = urlparse(self.endpoint)
            with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 80), timeout=0.3):
                return True
        except Exception:
            return False

    def _rpc_url(self, path: str = "/jsonrpc") -> str:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(self.endpoint if "://" in self.endpoint else f"http://{self.endpoint}")
        return urlunparse((parsed.scheme or "http", parsed.netloc, path, "", "", ""))

    async def _rpc(self, client: Any, method: str, params: list[Any]) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": "meiko", "method": method, "params": params}
        if self.name == "aria2" and self.secret:
            payload["params"] = [f"token:{self.secret}", *params]
        response = await client.post(self._rpc_url(), json=payload, timeout=self.timeout)
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            raise TransferError(f"{self.name} refused the task: {body['error'].get('message', 'unknown error')}")
        return body.get("result")

    async def fetch(self, url: str, destination: str, *, on_progress=None) -> str:
        if not self.available():
            raise TransferError(
                f"{self.name} daemon is not reachable at {self.endpoint or '(no endpoint configured)'}; "
                "start it and set daemon_endpoint, or drop the flag to use the local copier"
            )
        if self.name == "rclone":
            raise TransferError(
                "the rclone remote must be a directory target (mirror -rclone), not a URL fetch; "
                "use LeechTransferEngine.push with a CloudUploader"
            )
        import httpx

        async with httpx.AsyncClient() as client:
            if self.name == "aria2":
                gid = await self._rpc(client, "aria2.addUri", [[url], {"dir": str(Path(destination).parent)}])
                if not gid:
                    raise TransferError("aria2 did not return a download id")
                while True:
                    await asyncio.sleep(1.0)
                    status = await self._rpc(
                        client, "aria2.tellStatus", [gid, ["status", "completedLength", "totalLength", "errorMessage"]]
                    ) or {}
                    if on_progress is not None:
                        await on_progress(
                            int(status.get("completedLength") or 0), int(status.get("totalLength") or 0)
                        )
                    if status.get("status") == "complete":
                        return destination
                    if status.get("status") in {"error", "removed"}:
                        raise TransferError(
                            f"aria2 failed: {status.get('errorMessage') or status.get('status')}"
                        )
            raise TransferError(
                f"{self.name} RPC is reachable but this backend has no fetch implementation; "
                "qBittorrent is only used for torrents (not wired in this build)"
            )


@dataclass
class LeechTask:
    """One mirror/leech job."""

    id: str
    url: str
    user_id: int
    chat_id: int
    kind: str = "leech"  # leech | mirror
    state: TaskState = TaskState.QUEUED
    downloaded: int = 0
    total: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    destination: str = ""
    error: str = ""
    flags: set[str] = field(default_factory=set)
    message_id: int = 0

    @property
    def percent(self) -> float:
        if not self.total:
            return 0.0 if self.state is TaskState.QUEUED else min(99.0, self.downloaded / max(self.total, 1) * 100)
        return min(100.0, self.downloaded / self.total * 100)

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    @property
    def speed(self) -> float:
        return self.downloaded / self.elapsed if self.elapsed > 0.5 else 0.0

    @property
    def eta(self) -> int:
        speed = self.speed
        if speed <= 0 or not self.total:
            return 0
        return int(max(0, self.total - self.downloaded) / speed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "kind": self.kind,
            "state": self.state.value,
            "downloaded": self.downloaded,
            "total": self.total,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "destination": self.destination,
            "error": self.error,
            "flags": sorted(self.flags),
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "LeechTask | None":
        if not isinstance(data, dict) or not data.get("id"):
            return None
        return cls(
            id=str(data["id"]),
            url=str(data.get("url") or ""),
            user_id=int(data.get("user_id") or 0),
            chat_id=int(data.get("chat_id") or 0),
            kind=str(data.get("kind") or "leech"),
            state=TaskState(data.get("state") or "queued"),
            downloaded=int(data.get("downloaded") or 0),
            total=int(data.get("total") or 0),
            started_at=float(data.get("started_at") or 0),
            finished_at=float(data.get("finished_at") or 0),
            destination=str(data.get("destination") or ""),
            error=str(data.get("error") or ""),
            flags=set(data.get("flags") or []),
            message_id=int(data.get("message_id") or 0),
        )


def parse_flags(argument: str) -> tuple[list[str], set[str]]:
    """Split ``"-m -unzip url"`` into urls and flags (``-m`` → ``mirror``)."""
    urls: list[str] = []
    flags: set[str] = set()
    for token in (argument or "").split():
        if token.startswith("-") and len(token) > 1:
            flag = FLAG_ALIASES.get(token[1:].lower())
            if flag:
                flags.add(flag)
            continue
        urls.append(token)
    return urls, flags


class TaskManager:
    """Queue + concurrency + cancellation for mirror/leech tasks."""

    def __init__(
        self,
        backend: TransferBackend | None = None,
        *,
        max_active: int = 2,
        max_per_user: int = 2,
        history: int = 100,
    ) -> None:
        self.backend = backend or LocalBackend()
        self.max_active = max_active
        self.max_per_user = max_per_user
        self.history = history
        self.tasks: dict[str, LeechTask] = {}
        self._running: dict[str, asyncio.Task[Any]] = {}

    # -- submission -------------------------------------------------------
    def active_for(self, user_id: int) -> list[LeechTask]:
        return [
            task
            for task in self.tasks.values()
            if task.user_id == user_id and task.state in (TaskState.QUEUED, TaskState.ACTIVE)
        ]

    def can_accept(self, user_id: int) -> tuple[bool, str]:
        if not self.backend.available():
            return False, f"the {self.backend.name} transfer backend is unavailable"
        # queued jobs are not yet consuming a slot, so only count running ones
        running = [
            t for t in self.tasks.values() if t.user_id == user_id and t.state is TaskState.ACTIVE
        ]
        if len(running) >= self.max_per_user:
            return False, f"you already have {self.max_per_user} job(s) running"
        active = sum(1 for t in self.tasks.values() if t.state is TaskState.ACTIVE)
        if active >= self.max_active:
            return False, "the worker is at capacity; retry in a moment"
        return True, ""

    def create(self, url: str, user_id: int, chat_id: int, *, kind: str = "leech", flags: set[str] | None = None) -> LeechTask:
        task = LeechTask(
            id=uuid.uuid4().hex[:8],
            url=url,
            user_id=user_id,
            chat_id=chat_id,
            kind=kind,
            flags=flags or set(),
        )
        self.tasks[task.id] = task
        self._trim()
        return task

    def _trim(self) -> None:
        if len(self.tasks) <= self.history:
            return
        finished = sorted(
            (t for t in self.tasks.values() if t.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)),
            key=lambda t: t.finished_at,
        )
        for task in finished[: max(0, len(self.tasks) - self.history)]:
            self.tasks.pop(task.id, None)

    # -- execution --------------------------------------------------------
    async def run(
        self,
        task: LeechTask,
        destination: str,
        *,
        on_update: Callable[[LeechTask], Awaitable[None]] | None = None,
        interval: float = 3.0,
    ) -> LeechTask:
        task.state = TaskState.ACTIVE
        task.started_at = time.time()
        task.destination = destination
        last = 0.0

        async def progress(done: int, total: int) -> None:
            nonlocal last
            task.downloaded = done
            task.total = total
            now = time.time()
            if on_update is not None and now - last >= interval:
                last = now
                await on_update(task)

        try:
            await self.backend.fetch(task.url, destination, on_progress=progress)
            task.state = TaskState.DONE
        except asyncio.CancelledError:
            task.state = TaskState.CANCELLED
            raise
        except Exception as exc:
            task.state = TaskState.FAILED
            task.error = str(exc)
        finally:
            task.finished_at = time.time()
            if on_update is not None:
                await on_update(task)
        return task

    def cancel(self, task_id: str, *, user_id: int | None = None) -> bool:
        task = self.tasks.get(task_id)
        if task is None or task.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
            return False
        if user_id is not None and task.user_id != user_id:
            return False
        task.state = TaskState.CANCELLED
        task.finished_at = time.time()
        running = self._running.pop(task_id, None)
        if running is not None and not running.done():
            running.cancel()
        return True

    def stats(self) -> dict[str, Any]:
        counts = {state: 0 for state in TaskState}
        for task in self.tasks.values():
            counts[task.state] += 1
        done = [t for t in self.tasks.values() if t.state is TaskState.DONE]
        return {
            "total": len(self.tasks),
            "queued": counts[TaskState.QUEUED],
            "active": counts[TaskState.ACTIVE],
            "done": counts[TaskState.DONE],
            "failed": counts[TaskState.FAILED],
            "cancelled": counts[TaskState.CANCELLED],
            "bytes": sum(t.downloaded for t in done),
            "backend": self.backend.name,
            "backend_ready": self.backend.available(),
        }

    def user_report(self, user_id: int) -> dict[str, Any]:
        mine = [task for task in self.tasks.values() if task.user_id == user_id]
        return {
            "total": len(mine),
            "active": sum(1 for t in mine if t.state in (TaskState.QUEUED, TaskState.ACTIVE)),
            "done": sum(1 for t in mine if t.state is TaskState.DONE),
            "failed": sum(1 for t in mine if t.state is TaskState.FAILED),
            "bytes": sum(t.downloaded for t in mine),
        }


def guess_kind(flags: set[str]) -> str:
    return "mirror" if "mirror" in flags or "rclone" in flags else "leech"


def _hh_mm_ss(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def magnet_available() -> bool:
    """BitTorrent is only honest when a daemon or a client library is present."""
    import importlib.util

    return any(importlib.util.find_spec(name) for name in ("aria2p", "qbittorrentapi", "libtorrent"))


def pick_backend(
    flags: set[str] | None = None,
    *,
    endpoint: str = "",
    binary: str = "",
    secret: str = "",
    proxy: str = "",
    cookies: str = "",
) -> TransferBackend:
    """Resolve the backend a flag asks for, or explain why it cannot be used.

    Order of preference: an explicitly requested daemon (fails closed), then
    ``ytdl``, then plain HTTP.  Returning an unavailable backend instead of
    silently substituting one is deliberate — the task then fails with
    ``the <name> transfer backend is unavailable``, which is the truth.
    """
    flags = flags or set()
    from meiko.modules.toolchain import path_of

    if "ytdl" in flags:
        return YtDlpBackend(binary=binary or path_of("ffmpeg"), proxy=proxy, cookies=cookies)
    if "aria2" in flags:
        return DaemonBackend("aria2", endpoint, secret=secret)
    if "qbit" in flags:
        return DaemonBackend("qbittorrent", endpoint, secret=secret)
    if "rclone" in flags:
        return DaemonBackend("rclone", endpoint, secret=secret)
    return LocalBackend()


__all__ = [
    "CANCELLED",
    "DaemonBackend",
    "YtDlpBackend",
    "magnet_available",
    "pick_backend",
    "FLAG_ALIASES",
    "LeechTask",
    "LocalBackend",
    "TaskManager",
    "TaskState",
    "TransferBackend",
    "TransferError",
    "guess_kind",
    "parse_flags",
]
