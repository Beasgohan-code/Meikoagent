"""End-to-end routing: a real ``Dispatcher`` must reach the right package.

The failure this guards against is silent: several packages registered catch-all
``F.text`` handlers, so ``/testrename`` was consumed by the saver and the renamer
never answered.  The current dispatcher provides no error, no log and no
exception – only a missing reply.  These tests feed genuine aiogram ``Update``
objects through a real ``Dispatcher`` and assert who answered.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from meiko.core.commands import CommandRegistry
from meiko.db.models import Plugin, PluginInstallation
from meiko.plugins.context import PluginScope
from meiko.plugins.manager import PluginManager
from meiko.plugins.support import OwnCommand, command_name

TOKEN = "123456789:" + "Z" * 34
OWNER_TELEGRAM_ID = 4242


class RecordingSession(BaseSession):
    """Captures every Bot API call and answers with plausible objects."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[str, dict]] = []

    async def close(self) -> None:  # pragma: no cover - nothing to release
        pass

    async def make_request(self, bot: Bot, method, timeout: int | None = None):
        from aiogram.methods import GetMe, SendMessage

        payload = method.model_dump(exclude_none=True)
        if isinstance(method, GetMe):
            return {"id": 1, "is_bot": True, "first_name": "T", "username": "testbot"}
        self.sent.append((type(method).__name__, payload))
        if isinstance(method, SendMessage):
            chat = payload["chat_id"]
            return {
                "message_id": 900 + len(self.sent),
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": chat if isinstance(chat, dict) else {"id": int(chat), "type": "private"},
                "text": payload.get("text", ""),
            }
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    def replies(self) -> list[str]:
        return [payload.get("text", "") for name, payload in self.sent if name == "SendMessage"]


def make_update(text: str, bot: Bot) -> Update:
    sender = TgUser(id=OWNER_TELEGRAM_ID, is_bot=False, first_name="Tester").as_(bot)
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=OWNER_TELEGRAM_ID, type="private").as_(bot),
        from_user=sender,
        text=text,
    ).as_(bot)
    return Update(update_id=1, message=message)


# ------------------------------------------------------------------ filter
class TestOwnCommandFilter:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("/save", "save"),
            ("/save@MyBot t.me/x/1", "save"),
            ("/SAVE", "save"),
            ("/testrename", "testrename"),
            ("just text", "just"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_command_name(self, value, expected) -> None:
        assert command_name(value) == expected

    def test_accepts_a_message_object_too(self) -> None:
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=1, type="private"),
            text="/save",
        )
        assert OwnCommand(["save"])(message) is True

    def test_only_declared_commands_match(self) -> None:
        own = OwnCommand(["save", "saved"])
        assert own("/save") is True
        assert own("/saved") is True
        assert own("/testrename") is False
        assert own(None) is False


# -------------------------------------------------------------- dispatch
#: Importing the builtin package is what registers the implementations.
pytest.importorskip("meiko.plugins.builtin")


@pytest.fixture()
async def wired(db, cache, session):
    """Start the five interactive packages on one dispatcher."""
    from meiko.plugins.base import registry
    from meiko.services.core import BotService, UserService

    manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
    user = await UserService(session).get_or_create_telegram_user(OWNER_TELEGRAM_ID, username="router")
    bot_row = await BotService(session).register(user.id, token=TOKEN)
    recorder = RecordingSession()
    bot = Bot(TOKEN, session=recorder)
    dispatcher = Dispatcher()
    manifests = {manifest.slug: manifest for manifest in registry.manifests()}
    scope = PluginScope(bot_id=bot_row.id)
    for slug in ("saver", "renamer", "music", "post_maker", "keep_alive", "file_store"):
        manifest = manifests[slug]
        plugin = Plugin(slug=slug, name=manifest.name, category=manifest.category)
        session.add(plugin)
        await session.flush()
        install = PluginInstallation(
            plugin_id=plugin.id,
            bot_id=bot_row.id,
            scope="bot",
            state="enabled",
            config={k: v.get("default") for k, v in manifest.config_schema.items() if "default" in v},
        )
        session.add(install)
        await session.flush()
        await manager.start(slug, installation=install, scope=scope, bot=bot, dispatcher=dispatcher)
    yield bot, dispatcher, recorder
    await manager.stop_all()
    await bot.session.close()


class TestDispatcherRouting:
    @pytest.mark.parametrize(
        "text",
        [
            "/testrename Show.S01E02.1080p.mkv",  # renamer, not the saver
            "/clean Show.x265.mkv",
            "/musicstats",  # music
            "/saver",  # saver panel
            "/postpreview",  # post maker
            "/heartbeat",  # keep_alive
            "/storestats",  # file_store
        ],
    )
    async def test_command_reaches_its_own_package(self, wired, text: str) -> None:
        bot, dispatcher, recorder = wired
        recorder.sent.clear()
        await dispatcher.feed_update(bot, make_update(text, bot))
        assert recorder.replies(), f"{text} was swallowed before any plugin answered"

    async def test_saver_does_not_swallow_other_plugins_commands(self, wired) -> None:
        bot, dispatcher, recorder = wired
        recorder.sent.clear()
        await dispatcher.feed_update(bot, make_update("/autorename", bot))
        text = recorder.replies()[0]
        assert "Renamer" in text or "Template" in text

    async def test_plain_text_is_not_answered(self, wired) -> None:
        bot, dispatcher, recorder = wired
        recorder.sent.clear()
        await dispatcher.feed_update(bot, make_update("just chatting, no t.me link here", bot))
        assert recorder.replies() == []
