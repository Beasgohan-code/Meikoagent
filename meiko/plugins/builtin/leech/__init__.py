"""``leech`` plugin package — mirror/leech task engine with pluggable backends."""

from .manifest import MANIFEST
from .plugin import LeechPlugin  # noqa: F401 - re-exported for the manager
from .tasks import (
    DaemonBackend,
    LeechTask,
    LocalBackend,
    TaskManager,
    TaskState,
    TransferError,
    guess_kind,
    parse_flags,
)

__all__ = [
    "MANIFEST",
    "MANIFEST",
    "DaemonBackend",
    "LeechTask",
    "LocalBackend",
    "TaskManager",
    "TaskState",
    "TransferError",
    "guess_kind",
    "parse_flags",
]
