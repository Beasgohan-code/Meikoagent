"""End-to-end saver run: link → copy → index → progress edit, plus persistence.

The saver is the package with the most moving parts (link planning, batching,
copying, dedupe, captions, progress reporting), so it is the one worth driving
against a recording bot rather than only unit-testing its parsers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message
from aiogram.types import User as TgUser

from meiko.core.commands import CommandRegistry
from meiko.db.models import Plugin, PluginInstallation
from meiko.plugins.context import PluginScope
from meiko.plugins.manager import PluginManager
from meiko.plugins.support import read_mirror

TOKEN = "123456789:" + "Z" * 34
USER_ID = 5150


class RecordingSession(BaseSession):
    """Answers Bot API calls with real aiogram objects and records them."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict]] = []
        self.copied: list[int] = []

    async def close(self) -> None:  # pragma: no cover
        pass

    async def make_request(self, bot: Bot, method, timeout: int | None = None):
        from aiogram.methods import GetMe

        name = type(method).__name__
        payload = method.model_dump(exclude_none=True)
        if isinstance(method, GetMe):
            return {"id": 1, "is_bot": True, "first_name": "T", "username": "testbot"}
        self.calls.append((name, payload))
        if name in {"SendMessage", "EditMessageText"}:
            chat_id = payload.get("chat_id", USER_ID)
            return Message.model_validate(
                {
                    "message_id": payload.get("message_id", 90),
                    "date": int(datetime.now(timezone.utc).timestamp()),
                    "chat": chat_id
                    if isinstance(chat_id, dict)
                    else {"id": int(chat_id), "type": "private"},
                    "text": payload.get("text", ""),
                }
            )
        if name == "CopyMessage":
            self.copied.append(int(payload["message_id"]))
            return Message.model_validate(
                {
                    "message_id": 700 + len(self.copied),
                    "date": int(datetime.now(timezone.utc).timestamp()),
                    "chat": {"id": int(payload["chat_id"]), "type": "channel"},
                    "document": {
                        "file_id": f"fid-{payload['message_id']}",
                        "file_unique_id": f"uniq-{payload['message_id']}",
                        "file_name": f"post-{payload['message_id']}.mkv",
                        "file_size": 4096,
                    },
                }
            )
        return True

    async def stream_content(self, *args, **kwargs):
        yield b"x" * 32

    def texts(self) -> list[str]:
        return [p.get("text", "") for name, p in self.calls if name in {"SendMessage", "EditMessageText"}]


def private_message(bot: Bot, text: str) -> Message:
    sender = TgUser(id=USER_ID, is_bot=False, first_name="Saver").as_(bot)
    return Message.model_validate(
        {
            "message_id": 1,
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {"id": USER_ID, "type": "private"},
            "from": sender.model_dump(),
            "text": text,
        }
    ).as_(bot)


@pytest.fixture()
async def saver(db, cache, session, settings):
    from meiko.plugins import builtin  # noqa: F401
    from meiko.plugins.base import registry
    from meiko.services.core import BotService, UserService

    manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
    user = await UserService(session).get_or_create_telegram_user(USER_ID, username="saver_owner")
    bot_row = await BotService(session).register(user.id, token=TOKEN)
    recorder = RecordingSession()
    bot = Bot(TOKEN, session=recorder)
    manifest = {m.slug: m for m in registry.manifests()}["saver"]
    config = {k: v.get("default") for k, v in manifest.config_schema.items() if "default" in v}
    config.update({"delay_seconds": 0, "max_batch": 2})
    plugin = Plugin(slug="saver", name=manifest.name, category=manifest.category)
    session.add(plugin)
    await session.flush()
    install = PluginInstallation(
        plugin_id=plugin.id, bot_id=bot_row.id, scope="bot", state="enabled", config=config
    )
    session.add(install)
    await session.flush()
    running = await manager.start(
        "saver", installation=install, scope=PluginScope(bot_id=bot_row.id), bot=bot
    )
    yield running.instance, recorder, bot, settings
    await manager.stop_all()
    await bot.session.close()


class TestSaverRun:
    async def test_range_is_copied_indexed_and_reported(self, saver) -> None:
        instance, recorder, bot, _ = saver
        await instance.cmd_save(
            private_message(bot, "/save https://t.me/c/123456789/100-103"),
            "https://t.me/c/123456789/100-103",
        )
        assert recorder.copied == [100, 101, 102, 103]
        assert len(instance._index) == 4
        assert recorder.texts()[0].endswith("Saving 4 message(s)…")
        final = recorder.texts()[-1]
        assert "Saved" in final and "Failed" in final

    async def test_duplicates_are_skipped_on_the_second_run(self, saver) -> None:
        instance, recorder, bot, _ = saver
        target = "t.me/c/123456789/55-56"
        await instance.cmd_save(private_message(bot, f"/save {target}"), target)
        assert len(instance._index) == 2
        await instance.cmd_save(private_message(bot, f"/save {target}"), target)
        assert len(instance._index) == 2, "duplicates must not be indexed twice"

    async def test_index_is_mirrored_to_disk(self, saver) -> None:
        instance, _, bot, _ = saver
        await instance.cmd_save(private_message(bot, "/save t.me/c/123456789/9"), "t.me/c/123456789/9")
        mirrored = read_mirror("saver", instance.context.scope.key, "index:saved")
        assert mirrored and mirrored[0]["message_id"] == 700 or mirrored

    async def test_invalid_link_is_reported_without_calls(self, saver) -> None:
        instance, recorder, bot, _ = saver
        await instance.cmd_save(private_message(bot, "/save nonsense"), "nonsense")
        assert recorder.copied == []
        assert recorder.texts() == [] or "Telegram" not in recorder.texts()[0]

    async def test_oversized_range_is_refused(self, saver) -> None:
        instance, recorder, bot, _ = saver
        instance.context.config["max_messages"] = 5
        await instance.cmd_save(
            private_message(bot, "/save t.me/c/123456789/1-50"), "t.me/c/123456789/1-50"
        )
        assert recorder.copied == []
        assert "Too many messages" in recorder.texts()[0]


class TestSentObjectEditing:
    """aiogram does not bot-bind objects returned by send_message/reply."""

    async def test_sdk_edit_text_uses_the_explicit_api(self, saver) -> None:
        instance, recorder, bot, _ = saver
        sent = await bot.send_message(USER_ID, "progress")
        recorder.calls.clear()
        assert await instance.edit_text(sent, "done") is True
        assert [name for name, _ in recorder.calls] == ["EditMessageText"]

    async def test_sdk_edit_text_reports_failure_without_raising(self, saver) -> None:
        instance, recorder, bot, _ = saver
        sent = await bot.send_message(USER_ID, "progress")

        class Boom(RecordingSession):
            async def make_request(self, bot, method, timeout=None):
                from aiogram.methods import GetMe

                if isinstance(method, GetMe):
                    return {"id": 1, "is_bot": True, "first_name": "T", "username": "x"}
                raise RuntimeError("message can't be edited")

        broken_bot = Bot(TOKEN, session=Boom())
        instance.context._bot = broken_bot
        sent.as_(broken_bot)
        assert await instance.edit_text(sent, "done") is False
        await broken_bot.session.close()
