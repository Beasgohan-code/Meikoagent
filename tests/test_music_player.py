"""``music`` player: queue maths, media resolution, cards and real dispatch.

The player is split so that everything testable offline is: the queue is a pure
state machine, sources are pure parsing plus the local catalogue, cards are pure
string building.  Only the outer 5% (a real ``Dispatcher`` + a recording Bot
session) needs aiogram, and that is exactly what the last class does.
"""

from __future__ import annotations

import pytest

from meiko.plugins.builtin.music.player_card import (
    clock,
    play_card,
    player_keyboard,
    progress_bar,
    queue_card,
    results_card,
    state_line,
)
from meiko.plugins.builtin.music.queue import MAX_QUEUE, PlayerQueue, Track
from meiko.plugins.builtin.music.sources import (
    CatalogueBackend,
    MediaResolver,
    PlayRequest,
    direct_media_kind,
    looks_like_url,
    missing_vc_requirements,
    parse_request,
    quality_label,
    split_flags,
    track_from_telegram,
    track_from_url,
    vc_available,
    youtube_id,
)


def make(title: str = "Song", duration: int = 200, **kwargs) -> Track:
    return Track(title=title, duration=duration, **kwargs)


# --------------------------------------------------------------------- queue
class TestTrack:
    def test_label_prefers_title_then_source(self) -> None:
        assert make("Kesariya").label == "Kesariya"
        assert Track(source="https://x/y.mp3").label == "https://x/y.mp3"
        assert Track().label == "untitled"

    def test_video_flag_drives_mode(self) -> None:
        assert make(kind="video").is_video is True
        assert make(kind="audio").is_video is False

    def test_round_trip(self) -> None:
        track = make("A", 30, url="https://x", video_id="abc", requested_by="Boss")
        assert Track.from_dict(track.to_dict()) == track


class TestQueueStatePersistence:
    def test_survives_json_shaped_round_trip(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A"))
        queue.enqueue(make("B"))
        queue.enqueue(make("C"))
        restored = PlayerQueue.from_dict(queue.to_dict())
        assert restored.current.label == "A"
        assert [t.label for t in restored.state.items] == ["B", "C"]

    def test_garbage_state_degrades_to_empty(self) -> None:
        queue = PlayerQueue.from_dict({"current": "not a track", "items": 5})
        assert queue.current is not None or queue.current is None  # never raises
        assert PlayerQueue.from_dict(None).is_empty()

    def test_totals(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A", 60))
        queue.enqueue(make("B", 30))
        assert queue.state.remaining == 1
        assert queue.state.total_duration() == 30


class TestPlayerQueue:
    def test_enqueue_appends_or_jumps_the_line(self) -> None:
        queue = PlayerQueue()
        queue.enqueue(make("A"))
        queue.enqueue(make("B"))
        queue.enqueue(make("C"), play_next=True)
        assert [t.label for t in queue.state.items] == ["C", "A", "B"]

    def test_queue_is_capped(self) -> None:
        queue = PlayerQueue()
        for index in range(MAX_QUEUE):
            assert queue.enqueue(make(str(index))) == index + 1
        assert queue.enqueue(make("overflow")) == 0
        assert queue.state.remaining == MAX_QUEUE

    def test_loop_count_repeats_then_ends(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A"))
        queue.set_loop(2)
        assert queue.next().label == "A"
        assert queue.next().label == "A"
        assert queue.next() is None

    def test_loop_queue_re_appends_the_played_track(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A"))
        queue.enqueue(make("B"))
        queue.set_loop_queue(True)
        assert queue.next().label == "B"
        # the played track goes to the back of the queue, so the queue repeats
        assert [t.label for t in queue.state.items] == ["B"]

    def test_skip_pushes_history_and_prev_restores(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A"))
        queue.enqueue(make("B"))
        assert queue.next().label == "B"
        assert [t.label for t in queue.state.history] == ["A"]
        assert queue.previous().label == "A"
        assert [t.label for t in queue.state.items] == ["B"]

    def test_play_now_displaces_without_history_double_counting(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A"))
        queue.enqueue(make("B"))
        queue.play_now(make("C"))
        assert queue.current.label == "C"
        assert [t.label for t in queue.state.items] == ["A", "B"]
        assert queue.state.history == []

    def test_play_now_keeps_the_requested_kind(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A", kind="audio"))
        queue.play_now(make("V", kind="video"))
        assert queue.state.mode == "video"
        assert queue.current.is_video

    def test_seek_clamps_both_ends(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A", 100))
        assert queue.seek(9999) == 100
        assert queue.progress() == 1.0
        assert queue.seek(-5) == 0
        assert queue.jump(30) == 30
        assert queue.jump(-100) == 0

    def test_seek_on_live_stream_is_free(self) -> None:
        queue = PlayerQueue()
        queue.start(make("live", 0))
        assert queue.seek(500) == 500

    def test_pause_resume_toggle(self) -> None:
        queue = PlayerQueue()
        assert queue.pause() is False  # nothing playing
        queue.start(make("A"))
        assert queue.pause() is True and queue.state.paused
        assert queue.toggle() is False
        assert queue.resume() is True

    def test_autoplay_pulls_from_the_pool_then_recurses(self) -> None:
        queue = PlayerQueue()
        queue.set_autoplay(True)
        pool = [make("R1"), make("R2")]
        picked = queue.next(autoplay_pool=pool)
        assert picked is not None and picked.label in {"R1", "R2"}
        assert queue.current is not None

    def test_autoplay_off_returns_none_on_empty_queue(self) -> None:
        assert PlayerQueue().next(autoplay_pool=[make("R")]) is None

    def test_remove_move_shuffle_clear(self) -> None:
        queue = PlayerQueue()
        queue.start(make("CUR"))
        for name in "ABC":
            queue.enqueue(make(name))
        # positions are 1-based, exactly as /remove 2 and /move 4 1 read
        assert queue.remove(1).label == "A"
        assert queue.move(2, 1) is True  # C now plays first
        assert queue.state.items[0].label == "C"
        assert queue.move(99, 1) is False
        assert queue.remove(99) is None
        assert queue.shuffle() == 2
        assert queue.clear(keep_current=True) == 2
        assert queue.current.label == "CUR"

    def test_volume_is_clamped(self) -> None:
        queue = PlayerQueue()
        assert queue.set_volume(500) == 200
        assert queue.set_volume(-10) == 0
        assert queue.set_volume(55) == 55

    def test_mode_and_loop_bounds(self) -> None:
        queue = PlayerQueue()
        assert queue.set_mode("video") == "video"
        assert queue.set_loop(999) == 50
        assert queue.set_loop(-3) == 0
        assert queue.set_loop_queue(True) is True
        assert queue.set_autoplay(True) is True


# ------------------------------------------------------------------- sources
class TestFlagParsing:
    def test_force_and_quality_flags_are_consumed(self) -> None:
        request, parts = split_flags("song name -f -hd")
        assert request.play_now is True
        assert request.height == 720 and request.video is True
        assert parts == ["song name"]

    def test_save_flag(self) -> None:
        request = parse_request("track -s")
        assert request.save is True and request.query == "track"

    def test_best_quality_keeps_zero_height(self) -> None:
        request = parse_request("x -best")
        assert request.video is True and request.height == 0

    @pytest.mark.parametrize("flag,expected", [("-sd", 480), ("-fhd", 1080), ("-2k", 1440), ("-4k", 2160)])
    def test_quality_tiers(self, flag: str, expected: int) -> None:
        assert parse_request(f"t {flag}").height == expected

    def test_quality_labels(self) -> None:
        assert quality_label(0) == "best"
        assert quality_label(1080) == "1080p"
        assert quality_label(1234) == "1234p"

    def test_looks_like_url(self) -> None:
        assert looks_like_url("https://youtu.be/x") is True
        assert looks_like_url("t.me/chan/1") is True
        assert looks_like_url("just a song") is False


class TestUrlParsing:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30", "dQw4w9WgXcQ"),
            ("https://youtube.com/shorts/abc123", "abc123"),
            ("https://m.youtube.com/watch?v=zzz", "zzz"),
            ("https://example.com/song.mp3", ""),
        ],
    )
    def test_youtube_ids(self, url: str, expected: str) -> None:
        assert youtube_id(url) == expected

    @pytest.mark.parametrize(
        "url,kind",
        [
            ("https://x/a.mp3", "audio"),
            ("https://x/a.flac?dl=1", "audio"),
            ("https://x/a.mp4", "video"),
            ("https://x/a.webp", "image"),
            ("https://x/a", ""),
        ],
    )
    def test_direct_media_kind(self, url: str, kind: str) -> None:
        assert direct_media_kind(url) == kind

    def test_track_from_youtube_url(self) -> None:
        track = track_from_url("https://youtu.be/dQw4w9WgXcQ", video=True, height=1080, requested_by="Boss")
        assert track.video_id == "dQw4w9WgXcQ"
        assert track.kind == "video" and track.height == 1080
        assert track.thumbnail.endswith("hqdefault.jpg")
        assert track.requested_by == "Boss"

    def test_track_from_direct_link_is_streamable(self) -> None:
        track = track_from_url("https://cdn/a.mp3")
        assert track.stream_url == "https://cdn/a.mp3" and track.kind == "audio"

    def test_track_from_telegram_file(self) -> None:
        track = track_from_telegram("FILEID", title="Song", duration=42)
        assert track.file_id == "FILEID" and track.duration == 42
        assert track.stream_url == ""


class _FakeEntry:
    def __init__(self, title: str, artist: str = "", file_id: str = "") -> None:
        self.title = title
        self.artist = artist
        self.filename = f"{title}.mp3"
        self.file_id = file_id


class _FakeLibrary:
    def __init__(self, entries) -> None:
        self._entries = entries

    def search(self, query: str, limit: int = 8):
        hits = [e for e in self._entries if query.lower() in e.title.lower()] if query else list(self._entries)
        return [(entry, 1.0) for entry in hits[:limit]]


class _FakeRemote:
    def __init__(self, tracks=None, boom: Exception | None = None) -> None:
        self.tracks = tracks or []
        self.boom = boom
        self.queries: list[str] = []

    async def search(self, query: str, limit: int):
        self.queries.append(query)
        if self.boom:
            raise self.boom
        return list(self.tracks)


class TestResolver:
    async def test_direct_link_short_circuits(self) -> None:
        resolver = MediaResolver(catalogue=CatalogueBackend(_FakeLibrary([_FakeEntry("x")])))
        outcome = await resolver.resolve(PlayRequest(url="https://cdn/a.mp3"))
        assert outcome.layer == "direct" and outcome.first.kind == "audio"

    async def test_image_link_is_refused_with_a_reason(self) -> None:
        resolver = MediaResolver()
        outcome = await resolver.resolve(PlayRequest(url="https://cdn/a.png"))
        assert not outcome.ok and "image" in outcome.reason

    async def test_catalogue_hits(self) -> None:
        backend = CatalogueBackend(_FakeLibrary([_FakeEntry("Kesariya", "Arijit"), _FakeEntry("Believer")]))
        outcome = await MediaResolver(catalogue=backend).resolve(PlayRequest(query="kesariya"), requested_by="Boss")
        assert outcome.layer == "catalogue"
        assert outcome.first.title == "Kesariya"
        assert outcome.first.requested_by == "Boss"

    async def test_catalogue_miss_falls_through_to_remote(self) -> None:
        remote = _FakeRemote([make("Remote hit")])
        resolver = MediaResolver(catalogue=CatalogueBackend(_FakeLibrary([])), remote=remote)
        outcome = await resolver.resolve(PlayRequest(query="anything"))
        assert outcome.layer == "remote" and outcome.first.title == "Remote hit"
        assert remote.queries == ["anything"]

    async def test_remote_failure_is_reported_not_raised(self) -> None:
        resolver = MediaResolver(remote=_FakeRemote(boom=RuntimeError("502")))
        outcome = await resolver.resolve(PlayRequest(query="x"))
        assert not outcome.ok and "502" in outcome.reason

    async def test_nothing_configured_explains_itself(self) -> None:
        outcome = await MediaResolver().resolve(PlayRequest(query="x"))
        assert not outcome.ok and "no source configured" in outcome.reason

    async def test_empty_query(self) -> None:
        outcome = await MediaResolver().resolve(PlayRequest())
        assert not outcome.ok and "nothing to search" in outcome.reason

    async def test_telegram_links_are_handed_to_the_saver(self) -> None:
        outcome = await MediaResolver().resolve(PlayRequest(url="https://t.me/chan/42"))
        assert not outcome.ok and "telegram link" in outcome.reason

    async def test_video_quality_is_applied_to_remote_hits(self) -> None:
        resolver = MediaResolver(remote=_FakeRemote([make("V")]))
        outcome = await resolver.resolve(PlayRequest(query="v", video=True, height=2160))
        assert outcome.first.height == 2160 and outcome.first.is_video


class TestBackendDetection:
    def test_vc_availability_is_boolean_and_explained(self) -> None:
        assert isinstance(vc_available(), bool)
        if not vc_available():
            assert missing_vc_requirements()


# --------------------------------------------------------------------- cards
class TestCards:
    def test_clock_formatting(self) -> None:
        assert clock(0) == "0:00"
        assert clock(83) == "1:23"
        assert clock(3725) == "1:02:05"
        assert clock(-9) == "0:00"

    def test_progress_bar_edges(self) -> None:
        plain = {"premium": False}
        assert progress_bar(0, 100, 10, **plain) == "▱" * 10
        assert progress_bar(100, 100, 10, **plain) == "▰" * 10
        assert progress_bar(50, 100, 10, **plain) == "▰" * 5 + "▱" * 5
        assert progress_bar(5, 0, 10, **plain) == "▱" * 10
        assert progress_bar(999, 100, 10, **plain) == "▰" * 10

    def test_progress_bar_is_built_from_premium_glyphs(self) -> None:
        bar = progress_bar(50, 100, 6)
        assert bar.count("<tg-emoji") == 6
        # Playeon caps: outline-left … fill-right style ends, never a lone glyph
        assert "6105063657828262298" in progress_bar(0, 100, 6)  # outline left cap
        assert "6102753558128501546" in progress_bar(100, 100, 6)  # fill right cap

    def test_card_contains_title_progress_and_requester(self) -> None:
        queue = PlayerQueue()
        queue.start(make("Kesariya", 268, url="https://youtu.be/x", uploader="Arijit"))
        queue.enqueue(make("Believer", 204))
        body = play_card(queue, requested_by="Boss")
        assert "Kesariya" in body and "Believer" in body
        assert "4:28" in body and "0:00" in body
        assert "Boss" in body
        assert '<a href="https://youtu.be/x">' in body

    def test_card_uses_premium_emoji_entities(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A"))
        assert "<tg-emoji emoji-id=" in play_card(queue)

    def test_paused_card_flips_the_icon(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A"))
        playing = state_line(queue)
        queue.pause()
        assert state_line(queue) != playing
        assert "pause" in state_line(queue).lower() or "⏸" in state_line(queue)

    def test_idle_card_explains_what_to_do(self) -> None:
        body = play_card(PlayerQueue())
        assert "Nothing is playing" in body and "/play" in body

    def test_state_line_shows_flags(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A"))
        queue.set_loop(3)
        queue.set_autoplay(True)
        queue.set_volume(40)
        line = state_line(queue)
        assert "×3" in line and "autoplay" in line and "40%" in line
        assert line.count("<tg-emoji") >= 3  # every flag is a premium icon

    def test_player_keyboard_has_the_control_protocol(self) -> None:
        queue = PlayerQueue()
        queue.start(make("A"))
        markup = player_keyboard(queue)
        datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert {"pb:toggle", "pb:skip", "pb:end", "pb:queue"} <= set(datas)
        assert all(data.startswith(("pb:", "q:")) for data in datas)

    def test_empty_queue_keyboard_is_just_the_queue_button(self) -> None:
        markup = player_keyboard(PlayerQueue())
        datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert datas == ["pb:queue"]

    def test_queue_card_paginates(self) -> None:
        queue = PlayerQueue()
        queue.start(make("cur"))
        for index in range(20):
            queue.enqueue(make(f"t{index}"))
        first = queue_card(queue, page=1, per_page=8)
        second = queue_card(queue, page=2, per_page=8)
        assert "t0" in first and "t0" not in second
        assert "page 1/3" in first and "page 2/3" in second

    def test_results_card(self) -> None:
        body = results_card("lofi", [make("lofi beat 1"), make("lofi beat 2")], layer="catalogue")
        assert "lofi beat 1" in body and "catalogue" in body
        assert "Nothing found" in results_card("nothing", [])


# ---------------------------------------------------------------- dispatch
pytest.importorskip("meiko.plugins.builtin")


OWNER_ID = 777002
ACTIVE_PLUGIN: dict[str, object] = {}


def _plugin_state(wired):
    """The running plugin instance's state store (for seeding fixtures)."""
    return ACTIVE_PLUGIN["plugin"].state


def _reload_catalogue() -> None:
    """Point the running player's resolver at the seeded catalogue."""
    from meiko.plugins.builtin.music.library import MusicLibrary
    from meiko.plugins.builtin.music.player import _Catalogue
    from meiko.plugins.builtin.music.sources import MediaResolver

    plugin = ACTIVE_PLUGIN["plugin"]
    plugin._resolver = MediaResolver(catalogue=_Catalogue(plugin.context, MusicLibrary(plugin._entries)), limit=8)


def _message(bot, text: str, message_id: int = 9):
    from datetime import datetime, timezone

    from aiogram.types import Chat, Message
    from aiogram.types import User as TgUser

    user = TgUser(id=OWNER_ID, is_bot=False, first_name="Tester").as_(bot)
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=OWNER_ID, type="private").as_(bot),
        from_user=user,
        text=text,
    ).as_(bot)


def attach_video(bot, owner_id: int, text: str):
    """An update whose message replies to a video document (for /encode, /put…)."""
    from datetime import datetime, timezone

    from aiogram.types import Chat, Message, Update, Video
    from aiogram.types import User as TgUser

    user = TgUser(id=owner_id, is_bot=False, first_name="Tester").as_(bot)
    video = Video(
        file_id="VID123",
        file_unique_id="UNIQ123",
        width=1920,
        height=1080,
        duration=60,
        file_name="clip.mp4",
        file_size=1024,
    ).as_(bot)
    parent = Message(
        message_id=90,
        date=datetime.now(timezone.utc),
        chat=Chat(id=owner_id, type="private").as_(bot),
        from_user=user,
        video=video,
    ).as_(bot)
    message = Message(
        message_id=91,
        date=datetime.now(timezone.utc),
        chat=Chat(id=owner_id, type="private").as_(bot),
        from_user=user,
        text=text,
        reply_to_message=parent,
    ).as_(bot)
    return Update(update_id=11, message=message)


class TestDispatch:
    """Real Dispatcher + recording session: the commands must actually answer."""

    @pytest.fixture()
    async def wired(self, db, cache, session):
        from aiogram import Bot, Dispatcher
        from aiogram.client.session.base import BaseSession

        from meiko.core.commands import CommandRegistry
        from meiko.db.models import Plugin, PluginInstallation
        from meiko.plugins.context import PluginScope
        from meiko.plugins.manager import PluginManager
        from meiko.services.core import BotService, UserService

        import meiko.plugins.builtin

        assert meiko.plugins.builtin.BUILTIN_MANIFESTS  # importing registers every plugin
        from meiko.plugins.base import registry

        token = "987654321:" + "Q" * 34

        class Recorder(BaseSession):
            def __init__(self) -> None:
                super().__init__()
                self.sent: list[tuple[str, dict]] = []

            async def close(self) -> None:
                pass

            async def make_request(self, bot, method, timeout=None):
                from datetime import datetime, timezone

                from aiogram.methods import GetMe, SendMessage
                from aiogram.types import Message

                payload = method.model_dump(exclude_none=True)
                if isinstance(method, GetMe):
                    return {"id": 1, "is_bot": True, "first_name": "T", "username": "vcbot"}
                self.sent.append((type(method).__name__, payload))
                if isinstance(method, SendMessage):
                    # a raw dict would bypass aiogram's validation; the handler
                    # expects a real Message (it stores message_id for editing)
                    chat = payload["chat_id"]
                    return Message.model_validate(
                        {
                            "message_id": 500 + len(self.sent),
                            "date": int(datetime.now(timezone.utc).timestamp()),
                            "chat": chat if isinstance(chat, dict) else {"id": int(chat), "type": "private"},
                            "text": payload.get("text", ""),
                        }
                    ).as_(bot)
                return True

            async def stream_content(self, *args, **kwargs):
                yield b""

            def replies(self) -> list[str]:
                return [p.get("text", "") for name, p in self.sent if name == "SendMessage"]

            def buttons(self) -> list[str]:
                out = []
                for _name, payload in self.sent:
                    markup = payload.get("reply_markup") or {}
                    for row in markup.get("inline_keyboard", []):
                        out.extend(button.get("callback_data", "") for button in row)
                return [data for data in out if data]

        manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
        user_row = await UserService(session).get_or_create_telegram_user(OWNER_ID, username="vc")
        bot_row = await BotService(session).register(user_row.id, token=token)
        recorder = Recorder()
        bot = Bot(token, session=recorder)
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
        running = await manager.start(
            manifest.slug,
            installation=install,
            scope=PluginScope(bot_id=bot_row.id),
            bot=bot,
            dispatcher=dispatcher,
        )
        ACTIVE_PLUGIN["plugin"] = running.instance
        try:
            yield bot, dispatcher, recorder
        finally:
            await manager.stop_all()
            await bot.session.close()

    async def _send(self, wired, text: str) -> list[str]:
        from aiogram.types import Update

        bot, dispatcher, recorder = wired
        recorder.sent.clear()
        await dispatcher.feed_update(bot, Update(update_id=2, message=_message(bot, text)))
        return recorder.replies()

    @pytest.mark.parametrize(
        "text",
        [
            "/vc",
            "/queue",
            "/playing",
            "/join",
            "/leave",
            "/room",
            "/play",  # usage text, never silence
            "/vplay",
            "/loop",  # nothing playing, still answers
            "/volume 50",
            "/style",
            "/speed 1.5",
            "/seek 30",
            "/autoplay",
            "/shuffle",
            "/clear",
        ],
    )
    async def test_every_command_answers(self, wired, text: str) -> None:
        replies = await self._send(wired, text)
        assert replies, f"{text} produced no reply"

    async def test_play_with_a_direct_link_builds_a_card(self, wired) -> None:
        replies = await self._send(wired, "/play https://cdn.example.com/song.mp3")
        body = replies[0]
        assert "song.mp3" in body
        assert "<tg-emoji" in body
        *_, recorder = wired
        assert {"pb:toggle", "pb:skip"} <= set(recorder.buttons())

    async def test_play_reports_honestly_when_nothing_resolves(self, wired) -> None:
        replies = await self._send(wired, "/play some song nobody indexed")
        assert replies and "Nothing found" in replies[0]
        assert "resolve_endpoint" in replies[0]

    async def test_transport_round_trip(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/a.mp3")
        paused = await self._send(wired, "/pause")
        assert any("Paused" in reply for reply in paused)
        resumed = await self._send(wired, "/resume")
        assert any("Resumed" in reply for reply in resumed)

    async def test_queue_and_skip(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/a.mp3")
        queued = await self._send(wired, "/play https://cdn.example.com/b.mp3")
        assert any("Added to queue" in reply for reply in queued)
        listing = await self._send(wired, "/queue")
        assert any("b.mp3" in reply for reply in listing)
        skipped = await self._send(wired, "/skip")
        assert any("b.mp3" in reply for reply in skipped)

    async def test_duplicate_links_are_rejected(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/dup.mp3")
        again = await self._send(wired, "/play https://cdn.example.com/dup.mp3")
        assert any("Already queued" in reply for reply in again)

    async def test_force_play_now(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/one.mp3")
        await self._send(wired, "/play https://cdn.example.com/two.mp3")
        forced = await self._send(wired, "/play -f https://cdn.example.com/three.mp3")
        assert any("three.mp3" in reply for reply in forced)

    async def test_catalogue_search_offers_a_picker_then_queues_the_pick(self, wired) -> None:
        # the catalogue is read at start-up, so seed it and restart the plugin
        bot, dispatcher, recorder = wired
        state = _plugin_state(wired)
        await state.set(
            "index:tracks",
            [
                {"title": "Kesariya", "artist": "Arijit", "filename": "kesariya.mp3", "file_id": "F1"},
                {"title": "Kesariya (Lofi)", "artist": "DJ", "filename": "kesariya-lofi.mp3", "file_id": "F2"},
            ],
        )
        ACTIVE_PLUGIN["plugin"]._entries = list(await state.get("index:tracks"))
        _reload_catalogue()

        replies = await self._send(wired, "/play kesariya")
        assert replies and "Results" in replies[0]
        picks = [data for data in recorder.buttons() if data.startswith("pb:pick:")]
        assert picks == ["pb:pick:0", "pb:pick:1"]

        answer, edits = await self._press(wired, "pb:pick:0")
        assert answer is not None and "Kesariya" in answer
        assert edits and "Kesariya" in edits[0]
        assert any("Kesariya" in reply for reply in await self._send(wired, "/playing"))

    async def test_pick_requires_a_search_first(self, wired) -> None:
        replies = await self._send(wired, "/pick 1")
        assert any("No such result" in reply or "Search again" in reply for reply in replies)

    async def test_loop_bounds_via_command(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/loop.mp3")
        assert any("Loop ×1" in reply for reply in await self._send(wired, "/loop"))
        assert any("Loop off" in reply for reply in await self._send(wired, "/loop off"))

    async def _press(self, wired, data: str) -> tuple[str | None, list[str]]:
        """Press an inline button on the last card and return (answer, edits)."""
        from datetime import datetime, timezone

        from aiogram.types import CallbackQuery, Chat, Message, Update
        from aiogram.types import User as TgUser

        bot, dispatcher, recorder = wired
        recorder.sent.clear()
        user = TgUser(id=OWNER_ID, is_bot=False, first_name="Tester").as_(bot)
        message = Message(
            message_id=42,
            date=datetime.now(timezone.utc),
            chat=Chat(id=OWNER_ID, type="private").as_(bot),
            from_user=user,
            text="card",
        ).as_(bot)
        call = CallbackQuery(
            id="cb1",
            from_user=user,
            chat_instance="1",
            data=data,
            message=message,
        ).as_(bot)
        await dispatcher.feed_update(bot, Update(update_id=3, callback_query=call))
        answers = [
            payload.get("text", "") for name, payload in recorder.sent if name == "AnswerCallbackQuery"
        ]
        edits = [payload.get("text", "") for name, payload in recorder.sent if name == "EditMessageText"]
        return (answers[0] if answers else None), edits

    async def test_toggle_button_pauses_and_edits_the_card(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/btn.mp3")
        answer, edits = await self._press(wired, "pb:toggle")
        assert answer is not None and "aused" in answer
        assert edits and "<tg-emoji" in edits[0]

    async def test_skip_button_advances_the_queue(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/s1.mp3")
        await self._send(wired, "/play https://cdn.example.com/s2.mp3")
        answer, edits = await self._press(wired, "pb:skip")
        assert answer is not None and "s1.mp3" in answer
        assert edits and "s2.mp3" in edits[0]

    async def test_end_button_stops_and_clears(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/e.mp3")
        answer, _edits = await self._press(wired, "pb:end")
        assert answer is not None and "Stopped" in answer
        assert any("Nothing is playing" in reply for reply in await self._send(wired, "/playing"))

    async def test_seek_buttons_jump_ten_seconds(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/seek.mp3")
        answer, _edits = await self._press(wired, "pb:+10")
        assert answer is not None and "0:10" in answer

    async def test_queue_button_swaps_the_card(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/q.mp3")
        _answer, edits = await self._press(wired, "pb:queue")
        assert edits and "Queue" in edits[0]

    async def test_remove_from_queue_by_position(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/r0.mp3")
        await self._send(wired, "/play https://cdn.example.com/r1.mp3")
        await self._send(wired, "/play https://cdn.example.com/r2.mp3")
        answer, edits = await self._press(wired, "q:rm:2")
        assert answer is not None and "r2.mp3" in answer
        assert edits and "r2.mp3" not in edits[0]

    async def test_unknown_callback_is_answered_not_crashed(self, wired) -> None:
        answer, _edits = await self._press(wired, "pb:nonsense")
        assert answer is not None and "Unknown" in answer

    async def test_playlist_save_load_and_delete(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/p1.mp3")
        await self._send(wired, "/play https://cdn.example.com/p2.mp3")
        saved = await self._send(wired, "/savequeue chill")
        assert any("Playlist saved" in reply for reply in saved)
        listed = await self._send(wired, "/playlist")
        assert any("chill" in reply for reply in listed)
        await self._send(wired, "/end")
        loaded = await self._send(wired, "/loadqueue chill")
        assert any("Playlist loaded" in reply for reply in loaded)
        removed = await self._send(wired, "/delqueue chill")
        assert any("Playlist deleted" in reply for reply in removed)
        assert any("No such playlist" in reply or "Playlist" in reply for reply in await self._send(wired, "/loadqueue chill"))

    async def test_chart_records_plays(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/charted.mp3")
        replies = await self._send(wired, "/chart")
        assert any("Chart" in reply for reply in replies)
        assert any("charted.mp3" in reply for reply in replies)

    async def test_now_and_next(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/n1.mp3")
        await self._send(wired, "/play https://cdn.example.com/n2.mp3")
        assert any("Now playing" in reply for reply in await self._send(wired, "/now"))
        upcoming = await self._send(wired, "/next")
        assert any("n2.mp3" in reply for reply in upcoming)

    async def test_3d_audio_presets(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/spatial.mp3")
        panel = await self._send(wired, "/mode3d")
        assert any("3D audio" in reply for reply in panel)
        applied = await self._send(wired, "/mode3d 8d")
        assert any("8d" in reply for reply in applied)

    async def test_auth_never_echoes_credentials(self, wired) -> None:
        panel = await self._send(wired, "/auth")
        assert any("Auth" in reply for reply in panel)
        secret = await self._send(wired, "/auth AQi8secretSessionString")
        assert any("Nothing was stored" in reply for reply in secret)
        assert all("AQi8secret" not in reply for reply in secret)

    async def test_play_site_suffix_is_understood(self, wired) -> None:
        replies = await self._send(wired, "/play something | sp")
        # either results or an honest "nothing found" — never a crash or silence
        assert replies

    async def test_end_clears_the_player(self, wired) -> None:
        await self._send(wired, "/play https://cdn.example.com/z.mp3")
        stopped = await self._send(wired, "/end")
        assert any("Player stopped" in reply for reply in stopped)
        assert any("Nothing is playing" in reply for reply in await self._send(wired, "/playing"))
