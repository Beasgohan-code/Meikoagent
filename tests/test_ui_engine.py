"""Tests for the premium emoji catalogue and Meiko UI engine."""

from __future__ import annotations

import zlib

import pytest

from meiko.ui import Btn, RichBuilder, message, keyboard, strip_premium
from meiko.ui import emoji, premium_html, document_id
from meiko.ui.engine import user_mention


class TestPremiumEmoji:
    def test_known_ids_come_from_videl0(self):
        # ids verified against Beasgohan-code/Videl0 premium_emojis.py
        assert document_id("shield") == "6030445631921721471"
        assert document_id("check") == "5084979757905347540"
        assert document_id("cross") == "5210952531676504517"
        assert document_id("robot") == "6030400221232501136"

    def test_aliases_resolve(self):
        assert document_id("success") == document_id("check")
        assert emoji("error") == "❌"

    def test_html_form_is_tg_emoji(self):
        markup = premium_html("crown")
        assert markup.startswith('<tg-emoji emoji-id="')
        assert markup.endswith("</tg-emoji>")
        assert "👑" in markup

    def test_unknown_emoji_does_not_crash(self):
        assert emoji("does_not_exist")

    def test_strip_premium_returns_plain(self):
        assert strip_premium(premium_html("spark")) == "✨"


class TestMessageBuilder:
    def test_title_contains_premium_emoji_and_escaped_text(self):
        out = message("Meiko <Agent>", "robot").build()
        assert "tg-emoji" in out
        assert "&lt;Agent&gt;" in out

    def test_long_message_is_truncated(self):
        builder = message("Header")
        builder.line("x" * 5000)
        assert len(builder.build()) <= 4096

    def test_table_renders_monospace(self):
        builder = message("Stats").table(["Bot", "Uptime"], [["@meiko", "2h"], ["@clone", "1m"]])
        out = builder.build()
        assert "<pre>" in out
        assert "@meiko" in out

    def test_user_mention_links_by_id(self):
        assert 'href="tg://user?id=42"' in user_mention(42, "Nick")


class TestKeyboards:
    def test_button_style_and_premium_icon(self):
        markup = keyboard([[Btn("Start", callback_data="start", emoji="rocket", style="success")]])
        button = markup.inline_keyboard[0][0]
        assert button.style == "success"
        assert button.icon_custom_emoji_id == document_id("rocket")
        assert "🚀" in button.text

    def test_copy_text_button(self):
        markup = keyboard([[Btn("Copy", copy_text="token-123")]])
        assert markup.inline_keyboard[0][0].copy_text.text == "token-123"

    def test_plain_keyboard_has_no_custom_emoji(self):
        markup = keyboard([[Btn("Start", callback_data="s", emoji="rocket")]], premium_icons=False)
        assert markup.inline_keyboard[0][0].icon_custom_emoji_id is None

    def test_callback_data_limit_enforced(self):
        with pytest.raises(ValueError):
            keyboard([[Btn("x", callback_data="y" * 65)]])


class TestRichBuilder:
    def test_blocks_are_typed(self):
        rich = (
            RichBuilder()
            .heading("Release notes", 1)
            .paragraph("Layer live")
            .bullets(["one", "two"], ordered=True)
            .checklist([("done", True)])
            .table([["Bot", "State"], ["meiko", "ok"]])
            .code("pip install meiko", "bash")
            .build()
        )
        values = [str(getattr(b.type, "value", b.type)) for b in rich.blocks]
        assert len(values) == 6
        assert "heading" in values
        assert "table" in values

    def test_html_fallback_is_plain(self):
        rich = RichBuilder().heading("Hi").paragraph("there").bullets(["a"])
        html_out = rich.to_html()
        assert "<b>Hi</b>" in html_out
        assert "• a" in html_out

    def test_expandable_quote_supported(self):
        rich = RichBuilder().quote("collapsed text", credit="MEIKO").build()
        assert str(getattr(rich.blocks[0].type, "value", rich.blocks[0].type)) == "expandable_blockquote"


class TestWidgetAdapter:
    def test_send_widget_validates_size(self):
        from meiko.telegram.api.adapter import SendWidget

        payload = SendWidget(chat_id=1, html="<b>ok</b>", allow_queries=True)
        assert payload.__api_method__ == "sendWidget"

    def test_send_widget_rejects_oversized_html(self):
        from meiko.telegram.api.adapter import SendWidget

        with pytest.raises(ValueError):
            SendWidget(chat_id=1, html="a" * 40_000)

    def test_send_widget_rejects_incompressible_html(self):
        import os

        from meiko.telegram.api.adapter import SendWidget

        blob = os.urandom(9000).hex()  # 18 KB of incompressible text
        assert len(zlib.compress(blob.encode(), 9)) > 4096
        with pytest.raises(ValueError):
            SendWidget(chat_id=1, html=blob)

    def test_edit_message_widget_data_requires_target(self):
        from meiko.telegram.api.adapter import EditMessageWidgetData

        with pytest.raises(ValueError):
            EditMessageWidgetData(chat_data="{}")
        ok = EditMessageWidgetData(chat_id=1, message_id=2, chat_data="{}")
        assert ok.__api_method__ == "editMessageWidgetData"

    def test_widget_query_update_extraction(self):
        from meiko.telegram.api.adapter import extract_widget_update

        payload = {
            "update_id": 1,
            "widget_query": {
                "id": "8123",
                "from": {"id": 7, "is_bot": False, "first_name": "A"},
                "chat": {"id": 7, "type": "private", "first_name": "A"},
                "message_id": 10,
                "chat_instance": "c",
                "data": '{"stars": 4}',
            },
        }
        update = extract_widget_update(payload)
        assert update is not None
        assert update.widget_query is not None
        assert update.widget_query.json_data() == {"stars": 4}

    def test_non_widget_update_ignored(self):
        from meiko.telegram.api.adapter import extract_widget_update

        assert extract_widget_update({"update_id": 5, "message": {}}) is None
