"""Toolchain resolution + the optional extractors (yt-dlp), tested for real.

The point of these tests is not "the module imports". It is that the three
places which ask *"is ffmpeg here?"* get the same answer, that a half-installed
toolchain (ffmpeg without ffprobe — exactly what the bundled wheel gives you)
still produces usable metadata, and that the yt-dlp layers are invisible rather
than broken when the package is absent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from meiko.modules import toolchain
from meiko.plugins.builtin.leech.tasks import (
    LocalBackend,
    TaskManager,
    YtDlpBackend,
    magnet_available,
    pick_backend,
)
from meiko.plugins.builtin.music.sources import (
    MediaResolver,
    PlayRequest,
    YtDlpBackend as MusicExtractor,
)


@pytest.fixture(autouse=True)
def fresh_resolution():
    """Every test starts from a cold resolver, since it caches per process."""
    toolchain.clear_cache()
    yield
    toolchain.clear_cache()


@pytest.fixture()
def sample_video(tmp_path: Path) -> Path:
    """A real 3-second H.264/AAC file, skipped when ffmpeg cannot make one."""
    if not toolchain.has("ffmpeg"):
        pytest.skip("no ffmpeg to generate a sample with")
    target = tmp_path / "sample.mp4"
    command = [
        toolchain.path_of("ffmpeg"), "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:  # pragma: no cover - broken ffmpeg build
        pytest.skip("ffmpeg could not encode the sample")
    return target


class TestResolution:
    def test_path_wins_over_the_bundled_wheel(self, monkeypatch, tmp_path):
        fake = tmp_path / "ffmpeg"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))
        toolchain.clear_cache()
        found = toolchain.resolve("ffmpeg")
        assert found.available and found.source == "path" and found.path == str(fake)

    def test_env_override_is_used_when_it_points_at_a_binary(self, monkeypatch, tmp_path):
        fake = tmp_path / "custom-ffmpeg"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setenv(toolchain.FFMPEG_ENV, str(fake))
        toolchain.clear_cache()
        found = toolchain.resolve("ffmpeg")
        assert found.source == "env" and found.path == str(fake)

    def test_a_broken_override_does_not_hide_a_working_binary(self, monkeypatch, tmp_path):
        """A typo in MEIKO_FFMPEG_BIN must not disable a perfectly good install."""
        monkeypatch.setenv(toolchain.FFMPEG_ENV, str(tmp_path / "nope"))
        toolchain.clear_cache()
        bundled_or_path = toolchain.resolve("ffmpeg")
        assert bundled_or_path.source in {"path", "bundled", ""}
        if shutil.which("ffmpeg"):  # pragma: no cover - depends on the host
            assert bundled_or_path.source == "path"

    def test_env_override_is_ignored_when_empty(self, monkeypatch):
        monkeypatch.delenv(toolchain.FFMPEG_ENV, raising=False)
        toolchain.clear_cache()
        assert isinstance(toolchain.resolve("ffmpeg").path, str)

    def test_missing_and_complete_agree_with_each_tool(self):
        for name in ("ffmpeg", "ffprobe"):
            assert toolchain.has(name) is bool(toolchain.path_of(name))
        assert toolchain.complete() is (not toolchain.missing())

    def test_describe_names_the_source_of_each_binary(self):
        state = toolchain.describe()
        assert set(state) == {"ffmpeg", "ffprobe", "sources", "paths", "missing", "probe_fallback"}
        assert state["probe_fallback"] is (state["ffmpeg"] and not state["ffprobe"])
        for name in ("ffmpeg", "ffprobe"):
            assert state["sources"][name] in {"path", "env", "bundled", ""}
            if not state[name]:
                assert state["paths"][name] == ""

    def test_missing_lists_only_what_is_absent(self):
        state = toolchain.describe()
        expected = [n for n in ("ffmpeg", "ffprobe") if not state[n]]
        assert toolchain.missing() == expected


class TestBannerFallback:
    """ffprobe is a separate binary; the wheel only ships ffmpeg."""

    BANNER = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '/tmp/x.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:01:23.45, start: 0.000000, bitrate: 1200 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1), yuv420p, 1920x1080, 30 fps
  Stream #0:1[0x2](eng): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, 128 kb/s
  Stream #0:2[0x3](eng): Subtitle: subrip (srt)
"""

    def test_parses_the_same_shape_as_ffprobe(self):
        data = toolchain.parse_banner(self.BANNER)
        assert data["format"]["duration"] == "83"
        assert [s["codec_type"] for s in data["streams"]] == ["video", "audio", "subtitle"]
        video, audio = data["streams"][0], data["streams"][1]
        assert (video["codec_name"], video["width"], video["height"]) == ("h264", 1920, 1080)
        assert audio["codec_name"] == "aac" and audio["sample_rate"] == 48000
        assert audio["tags"]["language"] == "eng"

    def test_metadata_helper_reads_container_and_bitrate(self):
        info = toolchain.parse_metadata(self.BANNER)
        assert "mp4" in info["container"]
        assert info["bitrate"] == "1200 kb/s"

    def test_empty_input_is_not_an_error(self):
        assert toolchain.parse_banner("") == {"format": {"duration": "0", "source": "ffmpeg-banner"}, "streams": []}

    @pytest.mark.asyncio
    async def test_probe_uses_ffprobe_when_it_exists(self, sample_video):
        if not toolchain.has("ffprobe"):
            pytest.skip("ffprobe is not installed; the fallback path is covered below")
        data = await toolchain.probe(sample_video)
        assert data["streams"] and data["format"].get("duration")

    @pytest.mark.asyncio
    async def test_probe_falls_back_to_the_banner(self, sample_video):
        assert toolchain.has("ffmpeg")
        data = await toolchain.probe(sample_video)
        assert data["streams"], "no streams parsed from either ffprobe or the banner"
        kinds = {s["codec_type"] for s in data["streams"]}
        assert {"video", "audio"} <= kinds
        # the fallback says so, so the UI can be honest about it
        if not toolchain.has("ffprobe"):
            assert data["format"]["source"] == "ffmpeg-banner"

    @pytest.mark.asyncio
    async def test_probe_without_any_binary_explains_itself(self, monkeypatch):
        monkeypatch.setattr(toolchain, "has", lambda name: False, raising=True)
        with pytest.raises(FileNotFoundError):
            await toolchain.probe("/tmp/anything")


class TestYtDlpLayers:
    def test_availability_matches_the_importable_package(self):
        installed = MusicExtractor.module() is not None
        assert MusicExtractor().available() is installed
        assert YtDlpBackend().available() is installed

    @pytest.mark.asyncio
    async def test_music_search_says_how_to_install_when_absent(self, monkeypatch):
        monkeypatch.setattr(MusicExtractor, "module", staticmethod(lambda: None))
        with pytest.raises(RuntimeError, match="yt-dlp is not installed"):
            await MusicExtractor().search("anything", 3)

    @pytest.mark.asyncio
    async def test_leech_fetch_says_how_to_install_when_absent(self, monkeypatch):
        from meiko.plugins.builtin.leech.tasks import TransferError

        monkeypatch.setattr(YtDlpBackend, "module", staticmethod(lambda: None))
        with pytest.raises(TransferError, match="yt-dlp is not installed"):
            await YtDlpBackend().fetch("https://example.com/v", "/tmp/out")

    @pytest.mark.asyncio
    async def test_resolver_degrades_without_an_extractor(self):
        outcome = await MediaResolver(catalogue=None, remote=None, extractor=None).resolve(
            PlayRequest(query="nothing indexed")
        )
        assert outcome.ok is False
        assert "no source configured" in outcome.reason

    @pytest.mark.asyncio
    async def test_resolver_reports_extractor_errors_instead_of_raising(self):
        class Exploding:
            async def search(self, query: str, limit: int):
                raise RuntimeError("network down")

        outcome = await MediaResolver(extractor=Exploding()).resolve(PlayRequest(query="x"))
        assert outcome.ok is False and "yt-dlp error" in outcome.reason

    @pytest.mark.asyncio
    async def test_resolver_returns_extractor_hits_with_the_right_layer(self):
        from meiko.plugins.builtin.music.queue import Track

        class Fake:
            async def search(self, query: str, limit: int):
                return [Track(title="Found", source="https://x/1", url="https://x/1", duration=42)]

        outcome = await MediaResolver(extractor=Fake()).resolve(PlayRequest(query="x"), requested_by="dj")
        assert outcome.layer == "extract" and outcome.first is not None
        assert outcome.first.title == "Found" and outcome.first.requested_by == "dj"


class TestBackendSelection:
    def test_plain_jobs_use_the_dependency_free_backend(self):
        assert isinstance(pick_backend(set()), LocalBackend)
        assert isinstance(pick_backend({"mirror"}), LocalBackend)

    def test_ytdl_flag_selects_the_extractor(self):
        assert isinstance(pick_backend({"ytdl"}), YtDlpBackend)

    def test_daemon_flags_fail_closed_instead_of_silently_downgrading(self):
        for flag in ("aria2", "qbit", "rclone"):
            backend = pick_backend({flag})
            assert backend.name in {"aria2", "qbittorrent", "rclone"}
            assert backend.available() is False, "no daemon is running here"
            # and the task manager refuses with the reason, rather than copying
            ok, reason = TaskManager(backend).can_accept(1)
            assert ok is False and "unavailable" in reason

    def test_magnet_capability_is_reported_honestly(self):
        assert isinstance(magnet_available(), bool)

    @pytest.mark.asyncio
    async def test_a_daemon_job_never_reaches_the_local_copier(self):
        manager = TaskManager(pick_backend({"aria2"}))
        task = manager.create("http://example.com/f.bin", 1, -100, flags={"aria2"})
        await manager.run(task, "/tmp/never-written")
        assert task.state.value == "failed"
        assert "aria2" in task.error and "not wired" in task.error
        assert not os.path.exists("/tmp/never-written")

    def test_every_declared_backend_names_itself(self):
        for backend in (LocalBackend(), YtDlpBackend(), pick_backend({"aria2"})):
            assert backend.name and isinstance(backend.available(), bool)


class TestTelemetryAgreement:
    """The dashboard, the encoder and the player must not disagree."""

    def test_telemetry_reports_the_same_binaries_as_the_toolchain(self):
        from meiko.modules import telemetry

        state = telemetry.describe()
        assert state["media"]["ffmpeg"] is toolchain.has("ffmpeg")
        assert state["media"]["ffprobe"] is toolchain.has("ffprobe")
        assert state["media"]["missing"] == toolchain.missing()

    def test_encoder_runner_uses_the_shared_resolver(self):
        from meiko.plugins.builtin.encoder import runner

        assert runner.ffmpeg_path() == toolchain.path_of("ffmpeg")
        assert runner.tools_available() is toolchain.complete()
        assert runner.missing_tools() == toolchain.missing()

    def test_music_voice_chat_needs_the_client_and_ffmpeg(self):
        from meiko.plugins.builtin.music.sources import missing_vc_requirements, vc_available

        missing = missing_vc_requirements()
        assert vc_available() is (not missing)
        if not toolchain.has("ffmpeg"):
            assert "ffmpeg" in missing


@pytest.mark.skipif(sys.platform.startswith("win"), reason="posix permissions")
def test_a_non_executable_override_is_not_treated_as_usable(tmp_path):
    dead = tmp_path / "ffmpeg"
    dead.write_text("not a binary")
    os.environ[toolchain.FFMPEG_ENV] = str(dead)
    try:
        toolchain.clear_cache()
        assert toolchain.resolve("ffmpeg").path != str(dead)
    finally:
        os.environ.pop(toolchain.FFMPEG_ENV, None)
        toolchain.clear_cache()


class _FakeYdl:
    """Enough of ``yt_dlp.YoutubeDL`` to exercise our wiring, not upstream's code."""

    calls: list[dict] = []

    def __init__(self, options):
        self.options = options
        self.__class__.calls.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if url.startswith("ytsearch"):
            return {
                "entries": [
                    {"id": "abc123", "title": "First", "webpage_url": "https://youtu.be/abc123",
                     "duration": 61, "uploader": "Someone", "thumbnail": "https://img/1.jpg"},
                    {"id": "def456", "title": "Second", "webpage_url": "https://youtu.be/def456",
                     "duration": 120},
                ]
            }
        if download:
            target = self.options["outtmpl"]
            Path(target).write_bytes(b"x" * 2048)
            return {"id": "abc123", "title": "First"}
        return {"id": "abc123", "title": "First", "url": "https://cdn/stream.m4a",
                "duration": 61, "uploader": "Someone", "height": 720}

    def prepare_filename(self, info):
        return self.options["outtmpl"]


class _FakeModule:
    YoutubeDL = _FakeYdl


class TestYtDlpPlumbing:
    """The extractor branches are wired correctly, independent of the network."""

    @pytest.mark.asyncio
    async def test_music_search_maps_entries_to_tracks(self, monkeypatch):
        _FakeYdl.calls.clear()
        monkeypatch.setattr(MusicExtractor, "module", staticmethod(lambda: _FakeModule))
        tracks = await MusicExtractor().search("anything", 2)
        assert [t.title for t in tracks] == ["First", "Second"]
        assert tracks[0].video_id == "abc123" and tracks[0].duration == 61
        assert tracks[0].uploader == "Someone"
        # flat extraction: never download while searching
        assert _FakeYdl.calls[-1]["skip_download"] is True
        assert _FakeYdl.calls[-1]["extract_flat"] == "in_playlist"

    @pytest.mark.asyncio
    async def test_music_resolve_returns_a_stream_url(self, monkeypatch):
        monkeypatch.setattr(MusicExtractor, "module", staticmethod(lambda: _FakeModule))
        track = await MusicExtractor().resolve("https://youtu.be/abc123")
        assert track is not None
        assert track.stream_url == "https://cdn/stream.m4a"
        assert track.kind == "audio"

    @pytest.mark.asyncio
    async def test_music_resolve_returns_none_instead_of_raising(self, monkeypatch):
        class Broken:
            class YoutubeDL:
                def __init__(self, options): ...
                def __enter__(self):
                    return self
                def __exit__(self, *exc):
                    return False
                def extract_info(self, url, download=False):
                    raise RuntimeError("private video")

        monkeypatch.setattr(MusicExtractor, "module", staticmethod(lambda: Broken))
        assert await MusicExtractor().resolve("https://youtu.be/x") is None

    @pytest.mark.asyncio
    async def test_page_links_get_extracted_before_being_queued(self, monkeypatch):
        monkeypatch.setattr(MusicExtractor, "module", staticmethod(lambda: _FakeModule))
        outcome = await MediaResolver(extractor=MusicExtractor()).resolve(
            PlayRequest(url="https://youtu.be/abc123")
        )
        assert outcome.first is not None
        assert outcome.first.stream_url == "https://cdn/stream.m4a", "a page link is not playable as-is"

    @pytest.mark.asyncio
    async def test_a_plain_media_file_is_not_sent_through_the_extractor(self, monkeypatch):
        def explode():  # pragma: no cover - would prove the wrong branch ran
            raise AssertionError("extraction must not run for a direct media URL")

        monkeypatch.setattr(MusicExtractor, "resolve", lambda *a, **k: explode())
        outcome = await MediaResolver(extractor=MusicExtractor()).resolve(
            PlayRequest(url="https://cdn.example.com/song.mp3")
        )
        assert outcome.first is not None and outcome.first.stream_url.endswith("song.mp3")

    @pytest.mark.asyncio
    async def test_leech_fetch_writes_the_file_and_reports_progress(self, monkeypatch, tmp_path):
        _FakeYdl.calls.clear()
        monkeypatch.setattr(YtDlpBackend, "module", staticmethod(lambda: _FakeModule))
        target = tmp_path / "video.mp4"
        seen: list[tuple[int, int]] = []

        async def progress(done: int, total: int) -> None:
            seen.append((done, total))

        produced = await YtDlpBackend(binary=toolchain.path_of("ffmpeg")).fetch(
            "https://youtu.be/abc123", str(target), on_progress=progress
        )
        assert Path(produced).exists() and Path(produced).read_bytes() == b"x" * 2048
        options = _FakeYdl.calls[-1]
        assert options["quiet"] is True and options["noplaylist"] is True
        assert options["max_filesize"] == 2000 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_leech_fetch_refuses_when_no_file_appears(self, monkeypatch, tmp_path):
        from meiko.plugins.builtin.leech.tasks import TransferError

        class NoWrite(_FakeYdl):
            def extract_info(self, url, download=False):
                return {"id": "x"}

        class Mod:
            YoutubeDL = NoWrite

        monkeypatch.setattr(YtDlpBackend, "module", staticmethod(lambda: Mod))
        with pytest.raises(TransferError, match="no file was written"):
            await YtDlpBackend().fetch("https://youtu.be/x", str(tmp_path / "out.mp4"))

    @pytest.mark.asyncio
    async def test_leech_fetch_maps_extractor_errors_to_transfer_errors(self, monkeypatch, tmp_path):
        from meiko.plugins.builtin.leech.tasks import TransferError

        class Boom:
            class YoutubeDL:
                def __init__(self, options): ...
                def __enter__(self):
                    return self
                def __exit__(self, *exc):
                    return False
                def extract_info(self, url, download=False):
                    raise RuntimeError("unsupported URL")

        monkeypatch.setattr(YtDlpBackend, "module", staticmethod(lambda: Boom))
        with pytest.raises(TransferError, match="could not fetch this URL"):
            await YtDlpBackend().fetch("https://example.com/x", str(tmp_path / "o.mp4"))

    @pytest.mark.asyncio
    async def test_the_event_loop_stays_responsive(self, monkeypatch):
        """Extraction runs in a thread; a slow site must not block the bot."""
        import asyncio
        import time

        class Slow(_FakeYdl):
            def extract_info(self, url, download=False):
                time.sleep(0.15)
                return {"entries": []}

        class Mod:
            YoutubeDL = Slow

        monkeypatch.setattr(MusicExtractor, "module", staticmethod(lambda: Mod))
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.02)
                ticks += 1

        await asyncio.gather(MusicExtractor().search("x", 1), ticker())
        assert ticks == 5, "search blocked the event loop"


class TestTagFormats:
    """MP3 stays in-tree; other containers go through mutagen (when installed)."""

    def test_format_routing(self):
        from meiko.plugins.builtin.music import id3

        assert id3.supported_format("song.mp3") == "mp3"
        assert id3.supported_format("SONG.MP3") == "mp3"
        mutagen_installed = id3.mutagen_available()
        expected = "mutagen" if mutagen_installed else "unsupported"
        for name in ("track.m4a", "track.flac", "track.ogg", "track.opus"):
            assert id3.supported_format(name) == expected, name
        assert id3.supported_format("archive.zip") == "unsupported"
        assert id3.supported_format("no-extension") == "unsupported"

    def test_mutagen_presence_matches_importability(self):
        from meiko.plugins.builtin.music import id3

        import importlib.util

        try:
            installed = importlib.util.find_spec("mutagen") is not None
        except Exception:
            installed = False
        assert id3.mutagen_available() is installed

    def test_a_real_flac_round_trips_through_mutagen(self, tmp_path):
        from meiko.plugins.builtin.music import id3

        if not id3.mutagen_available():
            pytest.skip("mutagen is not installed")
        target = tmp_path / "track.flac"
        target.write_bytes(_minimal_flac(256, 4096))
        tags = id3.TrackTags(title="Night Drive", artist="Meiko", album="Tests", year="2026")
        result = id3.write_tag_file(str(target), tags, artwork=None)
        assert result.tags.title == "Night Drive"
        read_back = id3.read_tag_file(str(target))
        assert (read_back.title, read_back.artist, read_back.album) == ("Night Drive", "Meiko", "Tests")

    def test_a_real_m4a_round_trips_through_mutagen(self, tmp_path, sample_video):
        """sample_video is H.264/AAC in MP4 — exactly the container /tag must handle."""
        from meiko.plugins.builtin.music import id3

        if not id3.mutagen_available():
            pytest.skip("mutagen is not installed")
        target = tmp_path / "audio.m4a"
        shutil.copy(sample_video, target)
        tags = id3.TrackTags(title="Clip", artist="Meiko")
        id3.write_tag_file(str(target), tags, artwork=None)
        read_back = id3.read_tag_file(str(target))
        assert read_back.title == "Clip" and read_back.artist == "Meiko"

    def test_writing_a_non_audio_file_fails_loudly(self, tmp_path):
        from meiko.plugins.builtin.music import id3

        if not id3.mutagen_available():
            pytest.skip("mutagen is not installed")
        junk = tmp_path / "junk.m4a"
        junk.write_bytes(b"not a container")
        with pytest.raises(Exception):
            id3.write_tag_file(str(junk), id3.TrackTags(title="x"))


def _minimal_flac(min_block: int = 4096, max_block: int = 4096) -> bytes:
    """A tiny but valid FLAC stream (fLaC + STREAMINFO), enough for mutagen.

    ``min_block`` also bounds how much mutagen will read at the end of the file,
    so it has to be no larger than the padding below.
    """
    streaminfo = (
        b"\x80\x00\x00\x22"  # block header: last-block flag, type 0 (STREAMINFO), length 34
        + (min_block).to_bytes(2, "big")  # min block size
        + (max_block).to_bytes(2, "big")  # max block size
        + b"\x00\x00\x06"  # min frame
        + b"\x00\x00\x06"  # max frame
        + b"\x0a\xc4\x42\xf0"  # 44100 Hz, 2 channels, 16 bps
        + b"\x00\x00\x00"  # total samples
        + b"\x00" * 16  # md5
    )
    return b"fLaC" + streaminfo + b"\x00" * 64
