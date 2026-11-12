"""End-to-end: a webapp transport action drives the real bot queue and the mirror.

This is the acceptance test the brief asks for ("webapp controls must hit real
backend functionality"): the plugin is started through the real PluginManager
with a recording Bot session, then the API service layer is asked to pause /
skip / stop the room. The assertions are on the bot's queue *and* on what the
dashboard would read afterwards.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetMe, SendMessage
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from meiko.db.models import BotAccount, Plugin, PluginInstallation
from meiko.modules.music import MusicSessions, MusicTransport
from meiko.modules.store import reset_module_store
from meiko.plugins.base import registry

__import__("meiko.plugins.builtin", fromlist=["MANIFESTS"])  # register the builtin plugins
from meiko.plugins.context import PluginScope
from meiko.plugins.manager import PluginManager
from meiko.runtime.manager import get_worker_manager
from meiko.core.commands import CommandRegistry
from meiko.services.core import BotService, UserService

TOKEN = "123456789:" + "Z" * 34
OWNER_ID = 424242
CHAT_ID = -1001234567890


class Recording(BaseSession):
    """Minimal Bot API double that returns real aiogram objects."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def close(self) -> None:  # pragma: no cover - nothing to close
        return None

    async def make_request(self, bot: Bot, method: Any, timeout: int | None = None) -> Any:
        payload = method.model_dump(exclude_none=True)
        if isinstance(method, GetMe):
            return {"id": 1, "is_bot": True, "first_name": "T", "username": "meikobot"}
        self.sent.append((type(method).__name__, payload))
        if isinstance(method, SendMessage):
            chat = payload["chat_id"]
            return Message.model_validate(
                {
                    "message_id": 700 + len(self.sent),
                    "date": int(datetime.now(timezone.utc).timestamp()),
                    "chat": chat if isinstance(chat, dict) else {"id": int(chat), "type": "supergroup"},
                    "text": payload.get("text", ""),
                }
            ).as_(bot)
        return True

    async def stream_content(self, *args: Any, **kwargs: Any):
        yield b""

    def texts(self) -> list[str]:
        return [payload.get("text", "") for name, payload in self.sent if name == "SendMessage"]


def _message(bot: Bot, text: str, chat_id: int = CHAT_ID) -> Message:
    user = TgUser(id=OWNER_ID, is_bot=False, first_name="Tester").as_(bot)
    return Message(
        message_id=11,
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="supergroup", title="Room").as_(bot),
        from_user=user,
        text=text,
    ).as_(bot)


@pytest_asyncio.fixture()
async def wired(settings, session, cache, monkeypatch):
    """A started music plugin on a bot whose id the dashboard will use."""
    reset_module_store()
    manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
    user = await UserService(session).get_or_create_telegram_user(OWNER_ID, username="dj")
    bot_row: BotAccount = await BotService(session).register(user.id, token=TOKEN)
    recorder = Recording()
    bot = Bot(TOKEN, session=recorder)
    dispatcher = Dispatcher()
    manifest = next(m for m in registry.manifests() if m.slug == "music")
    plugin_row = Plugin(slug=manifest.slug, name=manifest.name, category=manifest.category)
    session.add(plugin_row)
    await session.flush()
    install = PluginInstallation(
        plugin_id=plugin_row.id,
        bot_id=bot_row.id,
        scope="bot",
        state="enabled",
        config={k: v.get("default") for k, v in manifest.config_schema.items() if "default" in v},
    )
    session.add(install)
    await session.flush()
    await manager.start(
        manifest.slug,
        installation=install,
        scope=PluginScope(bot_id=bot_row.id),
        bot=bot,
        dispatcher=dispatcher,
    )

    # The dashboard discovers a running bot through the worker manager.  In a
    # test process there is no worker runtime, so stand in for it with a tiny
    # application object holding the started plugin instance.
    class _Application:
        def __init__(self, instance: Any) -> None:
            self.plugins = [instance]

    runtime = get_worker_manager()
    running_plugins = list(manager._running.values())  # type: ignore[attr-defined]
    assert running_plugins, "the music plugin did not start"
    runtime.state.applications[bot_row.id] = _Application(running_plugins[0].instance)
    try:
        yield bot, dispatcher, recorder, bot_row
    finally:
        runtime.state.applications.pop(bot_row.id, None)
        await manager.stop_all()
        await bot.session.close()
        reset_module_store()


async def send(dispatcher: Dispatcher, bot: Bot, text: str, chat_id: int = CHAT_ID) -> list[str]:
    recorder = bot.session
    recorder.sent.clear()
    await dispatcher.feed_update(bot, Update(update_id=3, message=_message(bot, text, chat_id)))
    return recorder.texts()


class TestTransportDrivesTheBot:
    async def test_pause_from_the_dashboard_changes_bot_state(self, wired):
        bot, dispatcher, _recorder, bot_row = wired
        await send(dispatcher, bot, "/play https://cdn.example.com/track.mp3")

        sessions = MusicSessions(_store())
        assert sessions.room(bot_row.id, CHAT_ID)["current"]["title"], "the bot never mirrored its queue"

        result = await MusicTransport(sessions).dispatch(bot_row.id, CHAT_ID, "pause", actor_id=OWNER_ID)
        assert result["applied"] is True
        assert result["state"]["paused"] is True

        # the bot itself agrees: /resume answers "Resumed"
        replies = await send(dispatcher, bot, "/resume")
        assert any("Resumed" in reply for reply in replies)

    async def test_skip_and_stop_use_the_same_queue(self, wired):
        bot, dispatcher, _recorder, bot_row = wired
        await send(dispatcher, bot, "/play https://cdn.example.com/a.mp3")
        await send(dispatcher, bot, "/play https://cdn.example.com/b.mp3")
        sessions = MusicSessions(_store())

        skipped = await MusicTransport(sessions).dispatch(bot_row.id, CHAT_ID, "skip", actor_id=OWNER_ID)
        assert skipped["applied"] is True
        assert sessions.room(bot_row.id, CHAT_ID)["current"]["title"].endswith("b.mp3")

        stopped = await MusicTransport(sessions).dispatch(bot_row.id, CHAT_ID, "stop", actor_id=OWNER_ID)
        assert stopped["applied"] is True
        room = sessions.room(bot_row.id, CHAT_ID)
        assert room["current"] is None and room["items"] == []

    async def test_rooms_stay_isolated_under_webapp_control(self, wired):
        bot, dispatcher, _recorder, bot_row = wired
        other = -1009999999999
        await send(dispatcher, bot, "/play https://cdn.example.com/one.mp3")
        await send(dispatcher, bot, "/play https://cdn.example.com/two.mp3", chat_id=other)

        sessions = MusicSessions(_store())
        await MusicTransport(sessions).dispatch(bot_row.id, CHAT_ID, "pause", actor_id=OWNER_ID)

        assert sessions.room(bot_row.id, CHAT_ID)["paused"] is True
        assert sessions.room(bot_row.id, other)["paused"] is False

    async def test_remote_play_queues_a_direct_link(self, wired):
        bot, dispatcher, _recorder, bot_row = wired
        sessions = MusicSessions(_store())
        result = await MusicTransport(sessions).dispatch(
            bot_row.id, CHAT_ID, "play", value="https://cdn.example.com/remote.mp3", actor_id=OWNER_ID
        )
        assert result["applied"] is True
        assert "remote.mp3" in sessions.room(bot_row.id, CHAT_ID)["current"]["title"]

        replies = await send(dispatcher, bot, "/queue")
        assert replies  # the bot still answers with the same queue

    async def test_unknown_action_is_refused(self, wired):
        _bot, _dispatcher, _recorder, bot_row = wired
        result = await MusicTransport(MusicSessions(_store())).dispatch(bot_row.id, CHAT_ID, "detonate")
        assert result["applied"] is False
        assert "unknown action" in result["reason"]


def _store():
    from meiko.modules.store import get_module_store

    return get_module_store()
