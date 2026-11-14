"""Cryptographic helpers: token encryption, hashing, signed payloads."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from meiko.config import get_settings

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "enc:v1:"


class DecryptionError(RuntimeError):
    """Raised when a stored secret cannot be decrypted with the active key."""


def _key_file() -> Path:
    settings = get_settings()
    return settings.data_dir / ".encryption_key"


def _load_or_create_key() -> str:
    settings = get_settings()
    if settings.encryption_key:
        return settings.encryption_key
    path = _key_file()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = Fernet.generate_key().decode()
    settings.ensure_dirs()
    path.write_text(key, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - non POSIX filesystems
        logger.warning("could not restrict permissions on encryption key file")
    return key


def signing_secret(purpose: str) -> str:
    """A stable secret derived from the installation key, for signing tokens.

    Callers used to fall back to public literals ("meiko-linkshare",
    "meiko-links") or to guessable values built from the chat id when no key was
    configured, which makes the HMACs they produce forgeable.  Deriving from the
    key file keeps tokens stable across restarts while never being guessable, and
    keeps each purpose' keys independent.
    """
    import hashlib as _hashlib
    import hmac as _hmac

    root = _load_or_create_key().encode()
    return _hmac.new(root, f"meiko-signing:{purpose}".encode(), _hashlib.sha256).hexdigest()


def fernet() -> Fernet:
    return Fernet(_load_or_create_key().encode())


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret (bot token, webhook secret ...) for storage."""
    if not plaintext:
        return ""
    token = fernet().encrypt(plaintext.encode()).decode()
    return TOKEN_PREFIX + token


def decrypt_secret(stored: str) -> str:
    """Decrypt a value produced by :func:`encrypt_secret`."""
    if not stored:
        return ""
    if not stored.startswith(TOKEN_PREFIX):
        # Legacy / plaintext value – return as-is so rotation is non-breaking.
        return stored
    try:
        return fernet().decrypt(stored[len(TOKEN_PREFIX) :].encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - depends on external state
        raise DecryptionError("stored secret could not be decrypted") from exc


def mask_secret(value: str, keep: int = 4) -> str:
    """Return a display-safe fingerprint of a secret."""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 8}{value[-keep:]}"


def token_fingerprint(token: str) -> str:
    """Stable, non-reversible identifier for a bot token."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(digest_b64)
    actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(actual, expected)


def new_api_key(prefix: str = "mk") -> tuple[str, str]:
    """Create an API key. Returns ``(plaintext, sha256 hash)``."""
    raw = secrets.token_urlsafe(32)
    plaintext = f"{prefix}_{raw}"
    return plaintext, hashlib.sha256(plaintext.encode()).hexdigest()


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def sign_session(payload: dict[str, Any], ttl_seconds: int) -> str:
    """Create a tamper-proof, expiring session token."""
    settings = get_settings()
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_seconds
    body["n"] = secrets.token_hex(8)
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(settings.secret_key.encode(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_session(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        b64, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(settings.secret_key.encode(), b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    padded = b64 + "=" * (-len(b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < time.time():
        return None
    return payload
