"""Bot API 10.4 compatibility layer."""

from meiko.telegram.api.adapter import (
    AnswerWidgetQuery,
    EditMessageWidgetData,
    EditSharedWidgetData,
    RawBotMethod,
    SendSharedWidget,
    SendWidget,
    WidgetQuery,
    SharedWidgetQuery,
    WidgetUpdate,
    call_raw,
    extract_widget_update,
)

__all__ = [
    "AnswerWidgetQuery",
    "EditMessageWidgetData",
    "EditSharedWidgetData",
    "RawBotMethod",
    "SendSharedWidget",
    "SendWidget",
    "WidgetQuery",
    "SharedWidgetQuery",
    "WidgetUpdate",
    "call_raw",
    "extract_widget_update",
]
