"""Typed Bot API 10.4 compatibility adapter.

``aiogram`` 3.31 exposes Bot API 10.3 (rich messages, ephemeral messages, live
photos, managed bots, guest queries …) but not the 10.4 **widget** family.
Rather than patching the library, this module declares real
:class:`~aiogram.methods.base.TelegramMethod` subclasses, so they flow through
aiogram's own session, error handling and response validation:

    from meiko.telegram.api.adapter import SendWidget

    await bot(SendWidget(chat_id=1, html="<b>hi</b>", allow_queries=True))

Schemas in this file are transcribed from the official Bot API reference
(``sendWidget``, ``sendSharedWidget``, ``answerWidgetQuery``,
``editMessageWidgetData``, ``editSharedWidgetData``, ``Widget``,
``WidgetQuery``, ``SharedWidgetQuery``, ``WidgetQueryType*``).
"""

from __future__ import annotations

import json
import zlib
from typing import Any, ClassVar, Literal

from aiogram.methods.base import TelegramMethod, TelegramType
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    MessageEntity,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    ForceReply,
    ReplyParameters,
    User,
    Chat,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_WIDGET_HTML = 32_768
MAX_WIDGET_HTML_COMPRESSED = 4_096
MAX_WIDGET_DATA = 1_024

ReplyMarkup = InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | ForceReply


def _validate_widget_html(value: str) -> str:
    if not value:
        raise ValueError("widget html is required")
    size = len(value.encode())
    if size > MAX_WIDGET_HTML:
        raise ValueError(f"widget html is {size} bytes, the limit is {MAX_WIDGET_HTML}")
    compressed = len(zlib.compress(value.encode(), 9))
    if compressed > MAX_WIDGET_HTML_COMPRESSED:
        raise ValueError(
            f"widget html compresses to {compressed} bytes, the limit is "
            f"{MAX_WIDGET_HTML_COMPRESSED} – it will be rejected by Telegram"
        )
    return value


def _validate_data(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value.encode()) > MAX_WIDGET_DATA:
        raise ValueError(f"widget data exceeds {MAX_WIDGET_DATA} bytes")
    return value


# --------------------------------------------------------------------------- #
# Methods
# --------------------------------------------------------------------------- #
class SendWidget(TelegramMethod[Message]):
    """``sendWidget`` – send a message with an HTML widget (Bot API 10.4)."""

    __returning__ = Message
    __api_method__ = "sendWidget"

    chat_id: int | str
    html: str
    message_thread_id: int | None = None
    width: int | None = None
    height: int | None = None
    public_data: str | None = None
    chat_data: str | None = None
    caption: str | None = None
    parse_mode: str | None = None
    caption_entities: list[MessageEntity] | None = None
    show_caption_above_media: bool | None = None
    allow_queries: bool | None = None
    is_shared: bool | None = None
    disable_notification: bool | None = None
    protect_content: bool | None = None
    allow_paid_broadcast: bool | None = None
    message_effect_id: str | None = None
    reply_parameters: ReplyParameters | None = None

    @field_validator("html")
    @classmethod
    def _html(cls, value: str) -> str:
        return _validate_widget_html(value)

    @field_validator("public_data", "chat_data")
    @classmethod
    def _data(cls, value: str | None) -> str | None:
        return _validate_data(value)

    @field_validator("width")
    @classmethod
    def _width(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 2_000:
            raise ValueError("width must be positive logical pixels")
        return value

    @field_validator("height")
    @classmethod
    def _height(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 4_000:
            raise ValueError("height must be positive logical pixels")
        return value


class SendSharedWidget(TelegramMethod[Message]):
    """``sendSharedWidget`` – re-send a shared widget by its identifier."""

    __returning__ = Message
    __api_method__ = "sendSharedWidget"

    chat_id: int | str
    widget_id: str
    message_thread_id: int | None = None
    chat_data: str | None = None
    caption: str | None = None
    parse_mode: str | None = None
    caption_entities: list[MessageEntity] | None = None
    show_caption_above_media: bool | None = None
    disable_notification: bool | None = None
    protect_content: bool | None = None
    allow_paid_broadcast: bool | None = None
    message_effect_id: str | None = None
    reply_parameters: ReplyParameters | None = None
    reply_markup: ReplyMarkup | None = None

    @field_validator("chat_data")
    @classmethod
    def _data(cls, value: str | None) -> str | None:
        return _validate_data(value)


class AnswerWidgetQuery(TelegramMethod[bool]):
    """``answerWidgetQuery`` – respond to a widget query."""

    __returning__ = bool
    __api_method__ = "answerWidgetQuery"

    widget_query_id: str
    data: str | None = None

    @field_validator("data")
    @classmethod
    def _data(cls, value: str | None) -> str | None:
        return _validate_data(value)


class EditMessageWidgetData(TelegramMethod[bool]):
    """``editMessageWidgetData`` – change widget state of one message."""

    __returning__ = bool
    __api_method__ = "editMessageWidgetData"

    chat_id: int | str | None = None
    message_id: int | None = None
    inline_message_id: str | None = None
    public_data: str | None = None
    chat_data: str | None = None

    @model_validator(mode="after")
    def _target(self) -> "EditMessageWidgetData":
        if self.inline_message_id is None and (self.chat_id is None or self.message_id is None):
            raise ValueError("provide either inline_message_id or chat_id + message_id")
        return self

    @field_validator("public_data", "chat_data")
    @classmethod
    def _data(cls, value: str | None) -> str | None:
        return _validate_data(value)


class EditSharedWidgetData(TelegramMethod[bool]):
    """``editSharedWidgetData`` – change public data of a shared widget."""

    __returning__ = bool
    __api_method__ = "editSharedWidgetData"

    widget_id: str
    public_data: str | None = None
    cache_time: int | None = None
    inline_message_id: str | None = None

    @field_validator("public_data")
    @classmethod
    def _data(cls, value: str | None) -> str | None:
        return _validate_data(value)

    @field_validator("cache_time")
    @classmethod
    def _cache(cls, value: int | None) -> int | None:
        if value is not None and not 5 <= value <= 3600:
            raise ValueError("cache_time must be between 5 and 3600 seconds")
        return value


# --------------------------------------------------------------------------- #
# Types not (yet) in aiogram
# --------------------------------------------------------------------------- #
class WidgetData(BaseModel):
    """``Widget`` object attached to messages containing an HTML widget."""

    model_config = ConfigDict(extra="allow")

    public_data: str | None = None
    chat_data: str | None = None
    id: str | None = None


class WidgetQueryWallet(BaseModel):
    model_config = ConfigDict(extra="allow")

    ton_address: str | None = None
    wallet_card: dict[str, Any] | None = None


class WidgetQueryTypeData(BaseModel):
    type: Literal["data"] = "data"


class WidgetQueryTypeSignData(BaseModel):
    type: Literal["sign_data"] = "sign_data"
    wallet: WidgetQueryWallet | None = None


class WidgetQueryTypeSentTransaction(BaseModel):
    type: Literal["sent_transaction"] = "sent_transaction"
    wallet: WidgetQueryWallet | None = None


WidgetQueryType = (
    WidgetQueryTypeData | WidgetQueryTypeSignData | WidgetQueryTypeSentTransaction | dict[str, Any]
)


class WidgetQuery(BaseModel):
    """``WidgetQuery`` – query from a widget in a message sent by the bot."""

    model_config = ConfigDict(extra="allow")

    id: str
    from_user: User = Field(alias="from")
    chat: Chat | None = None
    message_id: int | None = None
    inline_message_id: str | None = None
    chat_instance: str = ""
    chat_type: str | None = None
    type: WidgetQueryType | None = None
    data: str = ""
    widget_id: str | None = None

    @property
    def data_bytes(self) -> bytes:
        return self.data.encode()

    def json_data(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.data)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except ValueError:
            return {"raw": self.data}


class SharedWidgetQuery(BaseModel):
    """``SharedWidgetQuery`` – query from a forwarded shared widget."""

    model_config = ConfigDict(extra="allow")

    id: str
    from_user: User = Field(alias="from")
    chat_instance: str = ""
    type: WidgetQueryType | None = None
    data: str = ""
    widget_id: str

    def json_data(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.data)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except ValueError:
            return {"raw": self.data}


class WidgetUpdate(BaseModel):
    """A widget related update extracted from a raw update payload."""

    model_config = ConfigDict(extra="allow")

    update_id: int
    widget_query: WidgetQuery | None = None
    shared_widget_query: SharedWidgetQuery | None = None

    @property
    def is_widget_update(self) -> bool:
        return self.widget_query is not None or self.shared_widget_query is not None


def extract_widget_update(payload: dict[str, Any]) -> WidgetUpdate | None:
    """Parse ``widget_query`` / ``shared_widget_query`` out of a raw update.

    aiogram drops update kinds it does not know, so the runtime extracts these
    *before* the dispatcher sees the update.
    """
    if not isinstance(payload, dict):
        return None
    if "widget_query" not in payload and "shared_widget_query" not in payload:
        return None
    try:
        return WidgetUpdate.model_validate(payload)
    except Exception:  # pragma: no cover - malformed payload from a proxy
        return None


#: Update kinds the adapter adds to ``allowed_updates``.
EXTRA_ALLOWED_UPDATES: tuple[str, ...] = ("widget_query", "shared_widget_query")

STYLE_TO_TELEGRAM = {
    "default": None,
    "primary": "primary",
    "success": "success",
    "danger": "danger",
    "link": "link",
}


class RawBotMethod(TelegramMethod[Any]):
    """Generic ``TelegramMethod`` for Bot API calls aiogram has not typed yet.

    ``payload`` is forwarded verbatim – nothing is invented – while still going
    through aiogram's session, error mapping (flood wait, bad request …) and
    response envelope validation.
    """

    __returning__ = Any  # type: ignore[assignment]

    method_name: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def __api_method__(self) -> str:  # type: ignore[override]
        return self.method_name


async def call_raw(bot: Any, method: str, payload: dict[str, Any] | None = None) -> Any:
    """Call an untyped Bot API method through aiogram's session.

    Used only for methods that aiogram has neither typed nor that we adapted
    here.  Prefer the typed adapters – they validate parameters locally.
    """
    return await bot(RawBotMethod(method_name=method, payload=payload or {}))


__all__ = [
    "AnswerWidgetQuery",
    "EditMessageWidgetData",
    "EditSharedWidgetData",
    "EXTRA_ALLOWED_UPDATES",
    "MAX_WIDGET_DATA",
    "MAX_WIDGET_HTML",
    "SharedWidgetQuery",
    "SendSharedWidget",
    "SendWidget",
    "WidgetData",
    "WidgetQuery",
    "WidgetQueryType",
    "WidgetQueryWallet",
    "WidgetUpdate",
    "call_raw",
    "extract_widget_update",
    "RawBotMethod",
]
