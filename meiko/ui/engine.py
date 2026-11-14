"""Meiko UI Engine — all Telegram output goes through this module.

Responsibilities
----------------
* premium custom emoji everywhere (with plain unicode fallback),
* reusable HTML message builders (rich text, not raw string soup),
* Rich Message (Bot API 10.1+) block composition: headings, lists, tables,
  expandable quotes, buttons, documents, thinking placeholders,
* inline keyboards with coloured button styles, premium icons and
  ``CopyTextButton``,
* automatic plain-text fallback for clients/bots that cannot render premium
  emoji or rich blocks.

The engine is framework-agnostic: it produces aiogram types, so the same
builders are used by the bot, the API (message previews) and the plugins.
"""

from __future__ import annotations

import html as html_lib
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichBlock,
    InputRichBlockButtons,
    InputRichBlockDetails,
    InputRichBlockDivider,
    InputRichBlockExpandableBlockQuotation,
    InputRichBlockFooter,
    InputRichBlockList,
    InputRichBlockListItem,
    InputRichBlockParagraph,
    InputRichBlockPreformatted,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichBlockThinking,
    InputRichMessage,
    RichBlockTableCell,
    RichMessageButton,
)

from meiko.ui.emoji import get as get_emoji
from meiko.ui.emoji import strip_premium

MAX_TEXT = 4096
MAX_CAPTION = 1024


def esc(value: Any) -> str:
    """Escape user supplied text for HTML parse mode."""
    return html_lib.escape(str(value), quote=False)


def i(name: str) -> str:
    """Premium emoji as HTML entity (inline shorthand used across the codebase)."""
    return get_emoji(name).html


def p(name: str) -> str:
    """Premium emoji as plain unicode."""
    return get_emoji(name).fallback


# --------------------------------------------------------------------------- #
# Rich text builders
# --------------------------------------------------------------------------- #
@dataclass
class MessageBuilder:
    """Composable HTML message builder with automatic truncation."""

    lines: list[str] = field(default_factory=list)
    premium: bool = True

    def emoji(self, name: str) -> str:
        return i(name) if self.premium else p(name)

    def title(self, text: str, emoji_name: str | None = None) -> "MessageBuilder":
        icon = f"{self.emoji(emoji_name)} " if emoji_name else ""
        self.lines.append(f"<b>{icon}{esc(text)}</b>")
        return self

    def subtitle(self, text: str) -> "MessageBuilder":
        self.lines.append(f"<blockquote>{esc(text)}</blockquote>")
        return self

    def line(self, text: str = "", raw: bool = False) -> "MessageBuilder":
        self.lines.append(text if raw else esc(text))
        return self

    def key_value(
        self, key: str, value: Any, emoji_name: str | None = None, value_raw: bool = False
    ) -> "MessageBuilder":
        icon = f"{self.emoji(emoji_name)} " if emoji_name else ""
        rendered = str(value) if value_raw else esc(value)
        self.lines.append(f"{icon}<b>{esc(key)}:</b> <code>{rendered}</code>")
        return self

    def bullet(self, text: str, emoji_name: str | None = None) -> "MessageBuilder":
        icon = f"{self.emoji(emoji_name)} " if emoji_name else "• "
        self.lines.append(f"{icon}{esc(text)}")
        return self

    def code(self, code: str, language: str | None = None) -> "MessageBuilder":
        cls = f' class="language-{esc(language)}"' if language else ""
        self.lines.append(f"<pre><code{cls}>{esc(code)}</code></pre>")
        return self

    def divider(self) -> "MessageBuilder":
        self.lines.append("━━━━━━━━━━━━━━━━━━")
        return self

    def blank(self) -> "MessageBuilder":
        self.lines.append("")
        return self

    def table(self, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> "MessageBuilder":
        """Monospace table that survives plain-text rendering."""
        header_cells = [esc(h) for h in header]
        rendered_rows = [[esc(c) for c in row] for row in rows]
        widths = [_visible_len(h) for h in header]
        for row in rendered_rows:
            for idx, cell in enumerate(row):
                if idx < len(widths):
                    widths[idx] = max(widths[idx], _visible_len(cell))

        def fmt(cells: Sequence[str]) -> str:
            padded = [
                cell + " " * max(0, widths[idx] - _visible_len(cell))
                if idx < len(widths)
                else cell
                for idx, cell in enumerate(cells)
            ]
            return " │ ".join(padded)
        out = [escaped_pre(" │ ".join(header_cells))]
        out.append("┼".join("─" * (w + 1) for w in widths))
        out.extend(fmt(row) for row in rendered_rows)
        self.lines.append("<pre>" + "\n".join(out) + "</pre>")
        return self

    def buttonless_markup_link(self, text: str, url: str) -> "MessageBuilder":
        self.lines.append(f'<a href="{html_lib.escape(url, quote=True)}">{esc(text)}</a>')
        return self

    def build(self, footer: str | None = None) -> str:
        body = "\n".join(self.lines)
        if footer:
            body += f"\n\n<i>{esc(footer)}</i>"
        if len(body) > MAX_TEXT:
            plain = strip_premium(body)
            body = plain[: MAX_TEXT - 4] + " …"
        return body

    def plain(self) -> str:
        return strip_premium("\n".join(self.lines))


def _visible_len(text: str) -> int:
    return len(strip_premium(text))


def escaped_pre(text: str) -> str:
    """Escape text that is already inside ``<pre>`` (entities still apply)."""
    return html_lib.escape(text, quote=False)


def message(title: str | None = None, emoji_name: str | None = None) -> MessageBuilder:
    builder = MessageBuilder()
    if title:
        builder.title(title, emoji_name)
    return builder


# --------------------------------------------------------------------------- #
# Inline keyboards
# --------------------------------------------------------------------------- #
ButtonStyle = str  # "primary" | "success" | "danger" | "link" | "default"


@dataclass(frozen=True)
class Btn:
    text: str
    callback_data: str | None = None
    url: str | None = None
    web_app_url: str | None = None
    emoji: str | None = None
    style: ButtonStyle | None = None
    copy_text: str | None = None
    switch_inline_query: str | None = None


def inline_button(btn: Btn) -> InlineKeyboardButton:
    """Build an :class:`InlineKeyboardButton` with premium icon + colour style."""
    text = f"{p(btn.emoji)} {btn.text}" if btn.emoji else btn.text
    payload: dict[str, Any] = {"text": text}
    if btn.callback_data is not None:
        # Telegram caps callback_data at 64 bytes; enforce here rather than fail at runtime.
        data = btn.callback_data.encode()
        if len(data) > 64:
            raise ValueError(f"callback_data is {len(data)} bytes, the limit is 64")
        payload["callback_data"] = btn.callback_data
    if btn.url:
        payload["url"] = btn.url
    if btn.web_app_url:
        payload["web_app"] = {"url": btn.web_app_url}
    if btn.copy_text is not None:
        payload["copy_text"] = CopyTextButton(text=btn.copy_text)
    if btn.switch_inline_query is not None:
        payload["switch_inline_query"] = btn.switch_inline_query
    if btn.emoji:
        payload["icon_custom_emoji_id"] = get_emoji(btn.emoji).document_id
    if btn.style and btn.style != "default":
        payload["style"] = btn.style
    return InlineKeyboardButton(**payload)


def keyboard(rows: Sequence[Sequence[Btn]], premium_icons: bool = True) -> InlineKeyboardMarkup:
    """Build a keyboard; pass ``premium_icons=False`` for plain-text buttons."""
    built: list[list[InlineKeyboardButton]] = []
    for row in rows:
        line = []
        for btn in row:
            b = btn if premium_icons else Btn(
                text=btn.text,
                callback_data=btn.callback_data,
                url=btn.url,
                web_app_url=btn.web_app_url,
                style=btn.style,
                copy_text=btn.copy_text,
                switch_inline_query=btn.switch_inline_query,
                emoji=None,
            )
            line.append(inline_button(b))
        built.append(line)
    return InlineKeyboardMarkup(inline_keyboard=built)


def confirm_keyboard(confirm_data: str, cancel_data: str = "cancel") -> InlineKeyboardMarkup:
    return keyboard(
        [
            [
                Btn("Confirm", callback_data=confirm_data, emoji="check", style="success"),
                Btn("Cancel", callback_data=cancel_data, emoji="cross", style="danger"),
            ]
        ]
    )


# --------------------------------------------------------------------------- #
# Rich messages
# --------------------------------------------------------------------------- #
def rich(text_html: str) -> InputRichMessage:
    return InputRichMessage(html=text_html)


def rich_markdown(markdown: str) -> InputRichMessage:
    return InputRichMessage(markdown=markdown)


class RichBuilder:
    """Builds a structured :class:`InputRichMessage` block document."""

    def __init__(self) -> None:
        self.blocks: list[InputRichBlock] = []

    def heading(self, text: str, size: int = 2) -> "RichBuilder":
        self.blocks.append(InputRichBlockSectionHeading(text=text, size=max(1, min(6, size))))
        return self

    def paragraph(self, text: str) -> "RichBuilder":
        self.blocks.append(InputRichBlockParagraph(text=text))
        return self

    def code(self, text: str, language: str | None = None) -> "RichBuilder":
        self.blocks.append(InputRichBlockPreformatted(text=text, language=language))
        return self

    def bullets(self, items: Sequence[str], ordered: bool = False) -> "RichBuilder":
        self.blocks.append(
            InputRichBlockList(
                items=[InputRichBlockListItem(blocks=[InputRichBlockParagraph(text=item)]) for item in items],
                ordered=ordered,
            )
        )
        return self

    def checklist(self, items: Sequence[tuple[str, bool]]) -> "RichBuilder":
        self.blocks.append(
            InputRichBlockList(
                items=[
                    InputRichBlockListItem(
                        blocks=[InputRichBlockParagraph(text=text)],
                        has_checkbox=True,
                        is_checked=done,
                    )
                    for text, done in items
                ]
            )
        )
        return self

    def table(
        self,
        rows: Sequence[Sequence[str]],
        header: bool = True,
        compact: bool = False,
        striped: bool = True,
        caption: str | None = None,
    ) -> "RichBuilder":
        cells = [
            [
                RichBlockTableCell(
                    align="left",
                    valign="middle",
                    text=cell,
                    is_header=(header and idx == 0),
                )
                for cell in row
            ]
            for idx, row in enumerate(rows)
        ]
        self.blocks.append(
            InputRichBlockTable(
                cells=cells, is_compact=compact, is_striped=striped, caption=caption
            )
        )
        return self

    def quote(self, text: str, credit: str | None = None) -> "RichBuilder":
        self.blocks.append(InputRichBlockExpandableBlockQuotation(text=text, credit=credit))
        return self

    def details(self, summary: str, blocks: Sequence[InputRichBlock], is_open: bool = False) -> "RichBuilder":
        self.blocks.append(
            InputRichBlockDetails(summary=summary, blocks=list(blocks), is_open=is_open)
        )
        return self

    def thinking(self, text: str = "Thinking…") -> "RichBuilder":
        self.blocks.append(InputRichBlockThinking(text=text))
        return self

    def divider(self) -> "RichBuilder":
        self.blocks.append(InputRichBlockDivider())
        return self

    def buttons(self, buttons: Sequence[RichMessageButton], align: str | None = None) -> "RichBuilder":
        self.blocks.append(InputRichBlockButtons(buttons=list(buttons), align=align))
        return self

    def footer(self, text: str) -> "RichBuilder":
        self.blocks.append(InputRichBlockFooter(text=text))
        return self

    def build(self) -> InputRichMessage:
        return InputRichMessage(blocks=self.blocks)

    def to_html(self) -> str:
        """Fallback rendering for bots/clients without rich message support."""
        return rich_to_html(self.blocks)


def rich_to_html(blocks: Sequence[InputRichBlock]) -> str:
    """Render rich blocks to plain HTML for fallback delivery."""
    out: list[str] = []
    for block in blocks:
        btype = getattr(block, "type", "")
        btype = str(getattr(btype, "value", btype))
        if btype in ("section_heading", "heading"):
            out.append(f"<b>{esc(block.text)}</b>")
        elif btype == "paragraph":
            out.append(esc(block.text))
        elif btype in ("preformatted", "pre", "code"):
            out.append(f"<pre>{esc(block.text)}</pre>")
        elif btype == "list":
            for item in block.items:
                prefix = "☑" if getattr(item, "is_checked", False) else "•"
                text = getattr(item, "text", None)
                if not text and getattr(item, "blocks", None):
                    text = " ".join(
                        getattr(b, "text", "") for b in item.blocks if getattr(b, "text", None)
                    )
                out.append(f"{prefix} {esc(text or '')}")
        elif btype == "table":
            for row in block.cells:
                out.append(" │ ".join(esc(c.text or "") for c in row))
        elif btype in ("blockquote", "block_quotation", "expandable_blockquote", "expandable_block_quotation", "pull_quotation"):
            out.append(f"<blockquote>{esc(getattr(block, 'text', ''))}</blockquote>")
        elif btype == "divider":
            out.append("───────────")
        elif btype == "footer":
            out.append(f"<i>{esc(block.text)}</i>")
    text = "\n".join(out)
    return text[:MAX_TEXT]


# --------------------------------------------------------------------------- #
# Small helpers used by handlers and the API preview endpoint
# --------------------------------------------------------------------------- #
def truncate(text: str, limit: int = MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def user_mention(user_id: int, name: str) -> str:
    """Working mention for users without a username."""
    safe = esc(name)[:64]
    return f'<a href="tg://user?id={int(user_id)}">{safe}</a>'


def duration(seconds: int | float | None) -> str:
    """Human readable duration, e.g. ``1d 4h 20m``."""
    if not seconds:
        return "permanent"
    remaining = int(seconds)
    parts: list[str] = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if remaining >= size:
            parts.append(f"{remaining // size}{label}")
            remaining %= size
    return " ".join(parts[:2]) or "0s"


def bytes_human(size: float | int | None) -> str:
    if not size:
        return "0 B"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def status_dot(active: bool) -> str:
    return i("check") if active else i("cross")


__all__ = [
    "Btn",
    "MessageBuilder",
    "RichBuilder",
    "confirm_keyboard",
    "duration",
    "esc",
    "i",
    "inline_button",
    "keyboard",
    "message",
    "p",
    "rich",
    "rich_markdown",
    "rich_to_html",
    "status_dot",
    "truncate",
    "user_mention",
]
