"""``filestore`` plugin package — multi-user vault with signed share links."""

from .collections import Collection, StoredFile, Vault, VaultError
from .links import LinkBook, ShareLink, issue, parse_start_payload, verify
from .manifest import MANIFEST
from .plugin import FileStorePlugin  # noqa: F401 - re-exported for the manager

__all__ = [
    "MANIFEST",
    "MANIFEST",
    "Collection",
    "LinkBook",
    "ShareLink",
    "StoredFile",
    "Vault",
    "VaultError",
    "issue",
    "parse_start_payload",
    "verify",
]
