"""``encoder``: FFmpeg command building, profiles, progress parsing, dispatch."""

from __future__ import annotations

import sys

import pytest

from meiko.plugins.builtin.encoder.plans import (
    EncodeSettings,
    build_command,
    duration_of,
    has_subtitle_stream,
    output_name,
    parse_audio_streams,
    parse_progress,
)
from meiko.plugins.builtin.encoder.runner import ffmpeg_path, missing_tools, tools_available


class TestProfiles:
    def test_defaults_are_runnable(self) -> None:
        assert EncodeSettings().validate() == []

    def test_invalid_values_are_listed_not_raised(self) -> None:
        problems = EncodeSettings(codec="vp9", preset="turbo", container="mov", audio_codec="mp9").validate()
        assert len(problems) == 4

    def test_avi_rejects_hevc_and_subtitle_copy(self) -> None:
        problems = EncodeSettings(codec="x265", container="avi", subtitles="copy").validate()
        assert any("HEVC" in p for p in problems)

    def test_round_trip_and_clamping(self) -> None:
        settings = EncodeSettings.from_dict({"crf": "999", "codec": "x264", "preset": "slow", "bogus": 1})
        assert settings.crf == 51 and settings.codec == "x264"
        assert EncodeSettings.from_dict(settings.to_dict()).to_dict() == settings.to_dict()

    def test_garbage_dict_falls_back_to_defaults(self) -> None:
        assert EncodeSettings.from_dict("nope").codec in {"x264", "x265"}


class TestCommand:
    def test_x265_command_shape(self) -> None:
        args = build_command("in.mkv", "out.mkv", EncodeSettings(codec="x265", crf=24, preset="slow", container="mkv"))
        assert args[0] == "ffmpeg"
        assert args[args.index("-c:v") + 1] == "libx265"
        assert args[args.index("-crf") + 1] == "24"
        assert args[args.index("-preset") + 1] == "slow"
        assert args[-1] == "out.mkv" and "pipe:1" in args

    def test_copy_mode_never_re_encodes(self) -> None:
        args = build_command("in.mp4", "out.mp4", EncodeSettings(codec="immediate"))
        assert args[args.index("-c:v") + 1] == "copy"
        assert "-crf" not in args

    def test_scale_filter_uses_target_height(self) -> None:
        args = build_command("in.mkv", "out.mkv", EncodeSettings(resolution="720p"))
        vf = args[args.index("-vf") + 1]
        assert "scale='min(1280,iw)':-2" in vf

    def test_watermark_text_is_escaped(self) -> None:
        args = build_command("in.mkv", "out.mkv", EncodeSettings(watermark="A:B 'c' 100%"))
        vf = args[args.index("-vf") + 1]
        assert "drawtext=" in vf
        assert "A\\:B \\'c\\' 100\\%" in vf

    def test_subtitle_modes(self) -> None:
        copy = build_command("i", "o", EncodeSettings(subtitles="copy"), has_subtitles=True)
        assert copy[copy.index("-c:s") + 1] == "ass"
        none = build_command("i", "o", EncodeSettings(subtitles="none"))
        assert "-sn" in none
        extract = build_command("i", "o", EncodeSettings(subtitles="extract"), has_subtitles=True)
        assert extract[extract.index("-c:s") + 1] == "srt"

    def test_burn_only_when_container_supports_it(self) -> None:
        burned = build_command("i.mkv", "o.mkv", EncodeSettings(subtitles="burn"), has_subtitles=True)
        assert "subtitles=filename='i.mkv'" in " ".join(burned)
        avi = build_command("i.avi", "o.avi", EncodeSettings(container="avi", subtitles="burn"), has_subtitles=True)
        assert "subtitles=" not in " ".join(avi)

    def test_audio_variants(self) -> None:
        stereo = build_command("i", "o", EncodeSettings(audio_codec="aac", audio_channels="stereo"))
        assert stereo[stereo.index("-ac") + 1] == "2"
        none = build_command("i", "o", EncodeSettings(), has_audio=False)
        assert "-an" in none
        copy = build_command("i", "o", EncodeSettings(audio_codec="copy"))
        assert copy[copy.index("-c:a") + 1] == "copy"

    def test_frame_rate_and_10bit(self) -> None:
        args = build_command("i", "o", EncodeSettings(frame_rate="23.976", pix_fmt_10bit=True))
        assert args[args.index("-r") + 1] == "24000/1001"
        assert args[args.index("-pix_fmt") + 1] == "yuv420p10le"

    def test_metadata_and_faststart(self) -> None:
        args = build_command("i", "o", EncodeSettings(container="mp4", metadata_title="MEIKO"))
        assert "title=MEIKO" in args
        assert "+faststart" in args

    def test_output_names_never_overwrite_the_source(self) -> None:
        assert output_name("Show.S01E01.mkv", EncodeSettings(codec="x265", crf=24)) == "Show.S01E01 [x265-24].mkv"
        assert output_name("movie.mp4", EncodeSettings(codec="x264", crf=28, container="mp4")) == "movie [x264-28].mp4"


class TestParsing:
    def test_progress_from_microseconds(self) -> None:
        stats = parse_progress("out_time_ms=120000000\nspeed=1.5x\nfps=30\n", 600)
        assert stats["percent"] == 20.0 and stats["seconds"] == 120.0
        assert stats["speed"] == "1.5x" and stats["fps"] == "30"

    def test_progress_from_clock(self) -> None:
        assert parse_progress("out_time=00:00:60.5\n", 121)["seconds"] == 60.5

    def test_progress_never_reaches_100_early(self) -> None:
        assert parse_progress("out_time_ms=999999999\n", 10)["percent"] == 99.9

    def test_audio_streams_and_subtitles(self) -> None:
        probe = {
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264"},
                {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2,
                 "tags": {"language": "eng", "title": "English"}},
                {"index": 2, "codec_type": "subtitle", "codec_name": "subrip"},
            ],
            "format": {"duration": "123.7"},
        }
        streams = parse_audio_streams(probe)
        assert streams == [
            {"index": 1, "codec": "aac", "channels": 2, "language": "eng", "title": "English", "bitrate": 0}
        ]
        assert has_subtitle_stream(probe) is True
        assert duration_of(probe) == 123
        assert duration_of({}) == 0


def test_tool_detection_is_honest():
    """``tools_available`` is strict, but a half-installed toolchain still probes.

    The bundled ``imageio-ffmpeg`` wheel ships ``ffmpeg`` without ``ffprobe``,
    so "ffmpeg present, ffprobe missing" is a real state and must not be
    reported as a broken install.
    """
    assert isinstance(tools_available(), bool)
    if not tools_available():
        assert missing_tools()
        for name in missing_tools():
            resolve = getattr(sys.modules["meiko.modules.toolchain"], "path_of")
            assert resolve(name) == ""


# ---------------------------------------------------------------- dispatch
pytest.importorskip("meiko.plugins.builtin")


class TestDispatch:
    OWNER = 880001

    @staticmethod
    def _message(bot, text, owner=880001):
        from tests.telegram_harness import make_message

        return make_message(bot, text, owner)

    @pytest.fixture()
    async def wired(self, db, cache, session):
        from aiogram import Bot, Dispatcher
        import meiko.plugins.builtin

        assert meiko.plugins.builtin.BUILTIN_MANIFESTS
        from meiko.core.commands import CommandRegistry
        from meiko.db.models import Plugin, PluginInstallation
        from meiko.plugins.base import registry
        from meiko.plugins.context import PluginScope
        from meiko.plugins.manager import PluginManager
        from meiko.services.core import BotService, UserService

        token = "555000111:" + "E" * 34
        from tests.telegram_harness import Recorder

        manager = PluginManager(plugins=registry, commands=CommandRegistry(), cache=cache)
        user = await UserService(session).get_or_create_telegram_user(self.OWNER, username="enc")
        bot_row = await BotService(session).register(user.id, token=token)
        recorder = Recorder()
        bot = Bot(token, session=recorder)
        dispatcher = Dispatcher()
        manifest = next(m for m in registry.manifests() if m.slug == "encoder")
        row = Plugin(slug=manifest.slug, name=manifest.name, category=manifest.category)
        session.add(row)
        await session.flush()
        install = PluginInstallation(
            plugin_id=row.id, bot_id=bot_row.id, scope="bot", state="enabled",
            config={k: v.get("default") for k, v in manifest.config_schema.items() if "default" in v},
        )
        session.add(install)
        await session.flush()
        await manager.start(manifest.slug, installation=install, scope=PluginScope(bot_id=bot_row.id),
                            bot=bot, dispatcher=dispatcher)
        try:
            yield bot, dispatcher, recorder
        finally:
            await manager.stop_all()
            await bot.session.close()

    async def _send(self, wired, text: str) -> list[str]:
        from aiogram.types import Update

        bot, dispatcher, recorder = wired
        recorder.sent.clear()
        await dispatcher.feed_update(bot, Update(update_id=7, message=self._message(bot, text)))
        return recorder.replies()

    async def test_help_shows_the_panel_and_tool_status(self, wired) -> None:
        replies = await self._send(wired, "/encodehelp")
        assert replies and "Encoder" in replies[0]
        assert "FFmpeg" in replies[0]

    @pytest.mark.parametrize(
        "text,fragment",
        [
            ("/enccrf 24", "Profile updated"),
            ("/encpreset slow", "Profile updated"),
            ("/enccodec x264", "Profile updated"),
            ("/encaudio aac 192k stereo", "Profile updated"),
            ("/encsubs burn", "Profile updated"),
            ("/encresolution 720p", "Profile updated"),
            ("/encwatermark MEIKO", "Profile updated"),
        ],
    )
    async def test_every_profile_command_applies(self, wired, text: str, fragment: str) -> None:
        replies = await self._send(wired, text)
        assert any(fragment in reply for reply in replies), (text, replies)

    async def test_bad_values_are_explained(self, wired) -> None:
        assert any("Usage" in r for r in await self._send(wired, "/enccodec vp9"))
        assert any("Usage" in r for r in await self._send(wired, "/enccrf abc"))
        assert any("Usage" in r for r in await self._send(wired, "/encsubs sideways"))

    async def test_encode_without_a_file_explains_the_reply_flow(self, wired) -> None:
        replies = await self._send(wired, "/encode")
        assert replies and "Reply to a file" in replies[0]

    async def test_encode_plans_when_ffmpeg_is_missing(self, wired) -> None:
        """With no ffmpeg the bot explains and prints the command instead of failing."""
        from meiko.modules import toolchain
        from tests.telegram_harness import attach as attach_video

        if toolchain.has("ffmpeg"):  # pragma: no cover - depends on the host
            pytest.skip("ffmpeg is installed; the planning path is not used")
        bot, dispatcher, recorder = wired
        recorder.sent.clear()
        update = attach_video(bot, self.OWNER, "/encode")
        await dispatcher.feed_update(bot, update)
        text = "\n".join(recorder.replies())
        assert "FFmpeg missing" in text and "ffmpeg -hide_banner" in text

    async def test_stats_and_cancel_answer(self, wired) -> None:
        assert any("Encoder stats" in r for r in await self._send(wired, "/encstats"))
        assert any("Nothing is running" in r for r in await self._send(wired, "/enccancel"))
