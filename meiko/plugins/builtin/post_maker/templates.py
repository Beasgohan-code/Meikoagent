"""Post templates: HTML blocks, button rows, safe substitution, entity validation.

Telegram rejects the whole message on malformed HTML, so every template is
*validated* before it is stored and *escaped* on substitution.  That is the
difference between a styled post and an error the user never sees.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre",
    "blockquote", "tg-spoiler", "span", "br",
}
TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[^<>]*)?)/?>")
PLACEHOLDER_RE = re.compile(r"\{([a-z_]{2,16})\}")
BUTTON_RE = re.compile(
    r"^\s*(?P<text>[^|]{1,64})\|\s*(?P<url>https?://\S+)\s*$"
)
MAX_CAPTION = 1024
MAX_LENGTH_SOFT = 3800

DEFAULT_BODY = (
    "<b>{title}</b>\n\n"
    "{description}\n\n"
    "<blockquote>{meta}</blockquote>"
)


@dataclass
class ButtonSpec:
    text: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {"text": self.text, "url": self.url}


@dataclass
class PostTemplate:
    name: str = "default"
    body: str = DEFAULT_BODY
    buttons: list[ButtonSpec] = field(default_factory=list)
    footer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "body": self.body,
            "buttons": [button.to_dict() for button in self.buttons],
            "footer": self.footer,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PostTemplate":
        return cls(
            name=str(data.get("name", "default")),
            body=str(data.get("body", DEFAULT_BODY)),
            buttons=[
                ButtonSpec(text=str(item.get("text", "")), url=str(item.get("url", "")))
                for item in data.get("buttons", [])
                if isinstance(item, dict)
            ],
            footer=str(data.get("footer", "")),
        )

    def markup_rows(self) -> list[list[ButtonSpec]]:
        """Two buttons per row, URL buttons only (safe by construction)."""
        rows = []
        for index in range(0, len(self.buttons), 2):
            rows.append(self.buttons[index : index + 2])
        return rows


@dataclass
class ValidationReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.errors:
            parts.append("errors: " + "; ".join(self.errors))
        if self.warnings:
            parts.append("warnings: " + "; ".join(self.warnings))
        return " | ".join(parts) or "looks good"


def validate_html(text: str) -> ValidationReport:
    """Balanced tags, allowed tag names, no unbalanced ``<``."""
    errors: list[str] = []
    warnings: list[str] = []
    stack: list[str] = []
    for match in TAG_RE.finditer(text or ""):
        whole = match.group(0)
        name = match.group(1).lower()
        if name not in ALLOWED_TAGS:
            errors.append(f"tag <{name}> is not supported by Telegram")
            continue
        if whole.startswith("</"):
            if not stack:
                errors.append(f"closing </{name}> without an opening tag")
            elif stack[-1] != name:
                errors.append(f"expected </{stack[-1]}> but found </{name}>")
                if name in stack:
                    while stack and stack.pop() != name:
                        pass
            else:
                stack.pop()
        elif whole.endswith("/>"):
            continue
        else:
            stack.append(name)
    if stack:
        errors.append("unclosed tag(s): " + ", ".join(f"<{name}>" for name in stack))

    unescaped = re.findall(r"<(?!/?(?:%s)\b)" % "|".join(ALLOWED_TAGS), text or "")
    if unescaped:
        warnings.append(f"{len(unescaped)} raw '<' may break parsing – escape as &lt;")
    if len(text or "") > MAX_LENGTH_SOFT:
        warnings.append(f"text is {len(text)} chars; Telegram caps captions at {MAX_CAPTION}")
    return ValidationReport(not errors, errors, warnings)


def render(template: str, values: dict[str, Any], *, escape: bool = True) -> str:
    """Substitute ``{placeholders}``; unknown ones are left untouched."""
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        raw = "" if values[key] is None else str(values[key])
        return html.escape(raw) if escape else raw

    return PLACEHOLDER_RE.sub(replace, template or "")


def render_post(template: PostTemplate, values: dict[str, Any]) -> str:
    body = render(template.body, values)
    footer = render(template.footer, values) if template.footer else ""
    text = f"{body}\n\n{footer}".strip() if footer else body.strip()
    return text[:MAX_LENGTH_SOFT]


def parse_buttons(text: str) -> list[ButtonSpec]:
    """Parse ``Label | https://url`` lines (one per line, max 8)."""
    buttons: list[ButtonSpec] = []
    for line in (text or "").splitlines():
        match = BUTTON_RE.match(line)
        if not match:
            continue
        buttons.append(ButtonSpec(text=match.group("text").strip(), url=match.group("url").strip()))
        if len(buttons) >= 8:
            break
    return buttons


def preview_text(template: PostTemplate, values: dict[str, Any]) -> str:
    """Plain-text preview for the webapp/panel (tags stripped, entities kept)."""
    rendered = render_post(template, values)
    return re.sub(r"<[^>]+>", "", rendered)


def placeholders_used(template: PostTemplate) -> set[str]:
    found = set(PLACEHOLDER_RE.findall(template.body or ""))
    found |= set(PLACEHOLDER_RE.findall(template.footer or ""))
    return found


def suggest_fill(template: PostTemplate) -> dict[str, str]:
    """Values used for a live preview in the panel."""
    return {
        "title": "Example post title",
        "description": "A short description rendered with Telegram entities.",
        "meta": "source: example · 2026-01-01",
        "channel": "@example",
        "link": "https://t.me/example/1",
        "tags": "#example #demo",
        "price": "₹0",
        "year": "2026",
    }


__all__ = [
    "ALLOWED_TAGS",
    "ButtonSpec",
    "MAX_CAPTION",
    "PostTemplate",
    "ValidationReport",
    "parse_buttons",
    "placeholders_used",
    "preview_text",
    "render",
    "render_post",
    "suggest_fill",
    "validate_html",
]
