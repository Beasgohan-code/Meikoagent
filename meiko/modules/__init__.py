"""Shared module infrastructure for the six media products.

``music``, ``saver``, ``encoder``, ``filestore``, ``linkshare`` and ``leech``
must not each grow their own queue, progress renderer, storage or metadata
handling.  This package is the single implementation they share:

======================  ====================================================
``store``               durable JSON state per module + scope (atomic writes)
``jobs``                priority job queue with concurrency, retry, cancel
``progress``            one Telegram progress renderer (premium emoji + bar)
``media``               metadata, MIME detection, hashing, temp workspaces
``events``              topic-based event hub feeding the webapp WebSocket
``mtproto``             WZgram/pyrogram adapter boundary (Bot API stays default)
======================  ====================================================
"""

from meiko.modules.events import EventHub, get_event_hub
from meiko.modules.filestore import FileStoreService, Shop, ShopError, ShopFile
from meiko.modules.leech import (
    CloudUploader,
    Downloader,
    HTTPDownloader,
    LocalDownloader,
    TelegramDownloader,
    TelegramUploader,
    TransferEngine,
    TransferError,
    TransferResult,
    Uploader,
)
from meiko.modules.ledger import JobLedger, LedgerEntry, QuotaExceeded, StorageLedger
from meiko.modules.linkshare import LinkError, LinkshareService, ShareLink
from meiko.modules.music import MusicSessions, MusicTransport
from meiko.modules.jobs import Job, JobQueue, JobState
from meiko.modules.media import MediaService, MediaInfo
from meiko.modules.progress import ProgressRenderer, ProgressView
from meiko.modules.store import ModuleStore, get_module_store

__all__ = [
    "CloudUploader",
    "Downloader",
    "EventHub",
    "FileStoreService",
    "HTTPDownloader",
    "Job",
    "LedgerEntry",
    "LinkError",
    "LinkshareService",
    "LocalDownloader",
    "MediaInfo",
    "MediaService",
    "ModuleStore",
    "MusicSessions",
    "MusicTransport",
    "ProgressRenderer",
    "ProgressView",
    "QuotaExceeded",
    "ShareLink",
    "Shop",
    "StorageLedger",
    "ShopError",
    "ShopFile",
    "TelegramDownloader",
    "TelegramUploader",
    "TransferEngine",
    "TransferError",
    "TransferResult",
    "Uploader",
    "JobLedger",
    "JobQueue",
    "JobState",
    "get_event_hub",
    "get_module_store",
]
