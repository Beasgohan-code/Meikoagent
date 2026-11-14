"""MTProto adapter boundary (WZgram / Pyrogram) — Bot API stays the default.

Meiko talks to Telegram through aiogram (Bot API).  A few things genuinely need
MTProto: streaming a user session into a voice chat, reading a channel the bot
was never added to but the *owner* is a member of, or higher upload limits.
Those are the only reasons this adapter exists, and it is strictly optional:

* ``available()`` is false unless ``wzgram``/``pyrogram`` is importable,
* no module may be *required* to work through it — every caller has a Bot API
  path and uses this only when both the dependency and an owner session exist,
* bot tokens and user sessions are never mixed: the adapter only ever loads a
  user session, and the bot identity stays in aiogram,
* nothing here bypasses an access restriction: ``can_read`` checks membership
  through the session and refuses when the account is not actually a member.

WZgram is a Pyrogram fork (LGPL-3.0); it is used as a *dependency* only, never
vendored, and its NOTICE/COPYING files stay with the installed package.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from typing import Any


def _find_client() -> tuple[str, Any] | tuple[None, None]:
    """Return ``(name, module)`` for the first importable MTProto client."""
    for name in ("wzgram", "pyrogram"):
        if importlib.util.find_spec(name) is not None:
            try:
                return name, importlib.import_module(name)
            except Exception:  # pragma: no cover - broken install
                continue
    return None, None


class MTProtoUnavailable(RuntimeError):
    """No MTProto client is installed, or no owner session is configured."""


@dataclass
class MTProtoCapabilities:
    available: bool = False
    client: str = ""
    reason: str = ""
    streaming: bool = False
    large_uploads: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "client": self.client,
            "reason": self.reason,
            "streaming": self.streaming,
            "large_uploads": self.large_uploads,
        }


def capabilities() -> MTProtoCapabilities:
    name, _module = _find_client()
    if not name:
        return MTProtoCapabilities(
            available=False,
            reason="install wzgram (or pyrogram) and configure an owner session to enable MTProto features",
        )
    from meiko.modules import toolchain

    return MTProtoCapabilities(
        available=True,
        client=name,
        reason="",
        # streaming really needs the binary, not a hopeful boolean
        streaming=toolchain.has("ffmpeg"),
        large_uploads=True,
    )


class MTProtoAdapter:
    """Typed wrapper around an optional user session.

    The adapter is intentionally thin: it does not own command routing, does not
    consume updates, and never receives bot tokens.  Its whole job is the three
    calls below plus the health check the webapp displays.
    """

    def __init__(self, *, session_string: str = "", api_id: int = 0, api_hash: str = "") -> None:
        self.session_string = session_string
        self.api_id = api_id
        self.api_hash = api_hash
        self._client: Any = None
        self.client_name = ""

    @property
    def configured(self) -> bool:
        return bool(self.session_string and self.api_id and self.api_hash)

    def available(self) -> bool:
        name, _module = _find_client()
        return bool(name) and self.configured

    def require(self) -> None:
        if not self.available():
            caps = capabilities()
            raise MTProtoUnavailable(
                caps.reason or "MTProto adapter is not configured (session/API credentials missing)"
            )

    async def start(self) -> bool:  # pragma: no cover - needs network + credentials
        self.require()
        name, module = _find_client()
        self.client_name = name or ""
        self._client = module.Client(
            name="meiko-mtproto",
            api_id=self.api_id,
            api_hash=self.api_hash,
            session_string=self.session_string,
            in_memory=True,
        )
        await self._client.start()
        return True

    async def can_read(self, chat: str | int) -> tuple[bool, str]:
        """Membership check *through the user session* — never a bypass."""
        if not self.available():
            return False, "MTProto adapter is not configured"
        if self._client is None:  # pragma: no cover - requires a live session
            return False, "session is not started"
        try:  # pragma: no cover
            await self._client.get_chat(chat)
            return True, ""
        except Exception as exc:  # pragma: no cover
            return False, f"the session cannot read {chat}: {exc}"

    async def stop(self) -> None:  # pragma: no cover - needs a live session
        if self._client is not None:
            await self._client.stop()
            self._client = None

    def health(self) -> dict[str, Any]:
        caps = capabilities().to_dict()
        caps["configured"] = self.configured
        caps["started"] = self._client is not None
        return caps


__all__ = [
    "MTProtoAdapter",
    "MTProtoCapabilities",
    "MTProtoUnavailable",
    "capabilities",
]
