"""``file_store`` plugin package.

Layout:
  manifest.py  typed manifest
  store.py     vault index + quota accounting (no aiogram imports)
  plugin.py    Telegram handlers, search and download
"""

from __future__ import annotations

from .manifest import MANIFEST
from .plugin import FileStorePlugin
from .store import StoreError, StoredFile, VaultIndex, describe_usage

__all__ = [
    "MANIFEST",
    "FileStorePlugin",
    "StoreError",
    "StoredFile",
    "VaultIndex",
    "describe_usage",
]
