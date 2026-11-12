"""One URL policy for every user-supplied link.

Six modules accept a URL from a stranger — linkshare creates one, filestore
stores one, leech downloads one, music plays one, saver copies one out of a
post, and an owner-configured relay fetches one on a bot's behalf. The upstream
repositories audited in ``docs/MODULES.md`` each solved this separately, and
some solved it badly. Doing it once means one place to audit and one place to
get right.

The rules, in order:

1. **http/https only.** ``file://``, ``gopher://``, ``data:`` and friends are
   refused, because every consumer of this module pipes the URL into a
   downloader or an outbound HTTP client.
2. **No credentials in the URL.** ``https://user:pass@host/`` leaks secrets into
   logs and is never what a user meant.
3. **No local or private targets.** Loopback, link-local (cloud metadata,
   ``169.254.169.254``), private ranges, ``.local``/``.internal``/``.lan`` and
   the unspecified address are refused. This is the SSRF guard: a bot that will
   happily fetch ``http://127.0.0.1:8000/api/v1/...`` is a confused deputy.
4. **No raw IP literals** whose ``ipaddress`` module calls private/reserved —
   including the IPv6 unique-local and link-local ranges.

Resolution *at request time* is deliberately **not** performed here: following
DNS in a pure validation function would make it flaky, cacheable in the wrong
place, and slow. The rule is therefore "no literal private address and no
private-looking hostname"; a downloader that resolves a public name to a
private address is a different (network-level) problem, stated in the docs
rather than pretended away.

Nothing in here fails open: an unparseable URL raises :class:`UrlRejected`.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

ALLOWED_SCHEMES = {"http", "https"}

#: Hostnames (or suffixes) that always mean "not the public internet".
BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
}
BLOCKED_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".lan", ".home.arpa", ".metadata")

#: Telegram file references are not URLs a downloader should fetch.
TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog"}

_MAX_LENGTH = 2048
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class UrlRejected(ValueError):
    """The URL is not something a module is willing to touch."""


def redact(url: str, limit: int = 100) -> str:
    """A log/UI-safe rendering: userinfo removed, length capped.

    URLs are user input and routinely carry tokens (``?token=…``) or
    credentials; the reason string is shown in chat and written to logs, so it
    never contains the raw URL.
    """
    parsed = urlparse(url or "")
    host = parsed.hostname or ""
    try:  # .port raises for out-of-range values, and redaction must never raise
        port = parsed.port
    except ValueError:
        port = None
    if port:
        host = f"{host}:{port}"
    safe = f"{parsed.scheme}://{host}{parsed.path}" if parsed.scheme else (parsed.path or "")
    safe = safe[:limit]
    return safe + ("…" if len(safe) == limit else "")


def _explain(reason: str, url: str) -> UrlRejected:
    shown = redact(url)
    return UrlRejected(f"{reason} ({shown})" if shown else reason)


def check(url: str, *, allow_telegram: bool = False) -> str:
    """Validate and normalise ``url``; return it stripped, or raise.

    ``allow_telegram`` keeps ``t.me`` links accepted (music does this: a
    Telegram post is resolved by the bot, not fetched over HTTP).
    """
    value = (url or "").strip()
    if not value:
        raise UrlRejected("empty URL")
    if len(value) > _MAX_LENGTH:
        raise _explain(f"URL is longer than {_MAX_LENGTH} characters", value)
    if _CONTROL_CHARS.search(value):
        raise _explain("URL contains control characters", value)

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise _explain(f"only http and https are allowed (got {scheme or 'no scheme'!r})", value)
    if parsed.username is not None or parsed.password is not None:
        raise _explain("credentials in a URL are not supported", value)

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise _explain("URL has no host", value)

    if host in TELEGRAM_HOSTS or host.endswith(tuple(f".{h}" for h in TELEGRAM_HOSTS)):
        if allow_telegram:
            return value
        raise _explain("Telegram links are resolved by the bot, not fetched", value)

    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        raise _explain("that host is not a public address", value)

    literal = _ip_literal(host)
    if literal is not None:
        if not _public(literal):
            raise _explain("that IP address is private, loopback or reserved", value)
        if parsed.port == 0:
            raise _explain("port 0 is not a valid destination", value)

    try:
        port = parsed.port
    except ValueError as exc:  # malformed port (e.g. "https://host:abc/")
        raise _explain("URL has an invalid port", value) from exc
    if port is not None and not (1 <= port <= 65535):
        raise _explain("URL has an invalid port", value)

    return value


def _ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """``is_global`` is the strictest, clearest test the stdlib offers."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_global
    return bool(address.is_global)


def is_allowed(url: str, *, allow_telegram: bool = False) -> bool:
    """Boolean form for UI code that shows a hint instead of raising."""
    try:
        check(url, allow_telegram=allow_telegram)
    except UrlRejected:
        return False
    return True


def reason(url: str, *, allow_telegram: bool = False) -> str:
    """Why a URL was refused, or ``""`` when it is fine."""
    try:
        check(url, allow_telegram=allow_telegram)
    except UrlRejected as exc:
        return str(exc)
    return ""


def canonical(url: str) -> str:
    """Lower-case scheme/host and drop an empty path, for de-duplication."""
    parsed = urlparse(check(url))
    host = (parsed.hostname or "").lower()
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.params, parsed.query, ""))


def _safe_port(parsed: Any) -> int | None:
    try:
        return parsed.port
    except ValueError:
        return None


def describe(url: str) -> dict[str, Any]:
    """Structured form (host, scheme, port, whether it is allowed) for APIs."""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    return {
        "url": url,
        "scheme": parsed.scheme.lower(),
        "host": host,
        "port": _safe_port(parsed),
        "allowed": is_allowed(url),
        "reason": reason(url),
    }


__all__ = [
    "ALLOWED_SCHEMES",
    "BLOCKED_HOSTNAMES",
    "UrlRejected",
    "canonical",
    "redact",
    "check",
    "describe",
    "is_allowed",
    "reason",
]
