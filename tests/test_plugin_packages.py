"""Tests for the Videl-style plugin *packages*.

Every plugin is a package with pure logic modules plus a thin aiogram layer, so
the tests exercise the real behaviour (link parsing, templating, ID3 writing,
watchdog state machine, sandbox) without a bot token, network or database.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from meiko.plugins.base import registry as plugin_registry
from meiko.plugins.builtin import BUILTIN_MANIFESTS

@pytest_asyncio.fixture()
async def app_client(settings):
    """Same app instance as tests/test_api.py, reused for the endpoint checks."""
    from httpx import ASGITransport, AsyncClient

    from meiko.api.app import create_app
    from meiko.cache import reset_cache

    await reset_cache()
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


NEW_SLUGS = {
    "saver",
    "renamer",
    "music",
    "post_maker",
    "keep_alive",
    "file_store",
    "code_runner",
}


# --------------------------------------------------------------- packaging
class TestPackageLayout:
    @pytest.mark.parametrize("slug", sorted(NEW_SLUGS))
    def test_is_a_package_not_a_single_file(self, slug: str) -> None:
        root = Path(__file__).resolve().parents[1] / "meiko" / "plugins" / "builtin" / slug
        assert root.is_dir(), f"{slug} must be a package directory"
        assert (root / "__init__.py").is_file()
        assert (root / "manifest.py").is_file()
        assert (root / "plugin.py").is_file()
        modules = [p for p in root.glob("*.py") if p.name not in {"__init__.py", "manifest.py", "plugin.py"}]
        assert modules, f"{slug} should carry its own logic modules, not one giant file"

    @pytest.mark.parametrize("slug", sorted(NEW_SLUGS))
    def test_manifest_is_registered(self, slug: str) -> None:
        registered = {manifest.slug for manifest in BUILTIN_MANIFESTS}
        assert slug in registered
        assert slug in set(plugin_registry.slugs())

    def test_no_plugin_file_is_a_plain_module(self) -> None:
        builtin = Path(__file__).resolve().parents[1] / "meiko" / "plugins" / "builtin"
        stray = [path.name for path in builtin.glob("*.py") if path.name != "__init__.py"]
        assert stray == [], f"single-file plugins are not allowed: {stray}"

    def test_manifests_validate_and_document_commands(self) -> None:
        for manifest in BUILTIN_MANIFESTS:
            manifest.validate()
            assert manifest.config_schema or manifest.slug in {"integrations"}
            assert not manifest.validate_config_schema(), manifest.slug

    def test_new_plugins_declare_commands_and_permissions(self) -> None:
        by_slug = {manifest.slug: manifest for manifest in BUILTIN_MANIFESTS}
        for slug in NEW_SLUGS - {"integrations"}:
            manifest = by_slug[slug]
            assert manifest.commands, slug
            assert manifest.permissions, slug
            assert manifest.description, slug


# --------------------------------------------------------------- saver links
class TestSaverLinks:
    def test_public_and_private_links(self) -> None:
        from meiko.plugins.builtin.saver import parse_link

        public = parse_link("https://t.me/durov/42")
        assert public is not None and public.chat == "durov"
        assert public.message_ids == [42]

        private = parse_link("t.me/c/123456789/77")
        assert private is not None and private.chat == -100123456789
        assert private.message_ids == [77]

    def test_ranges_lists_and_open_ranges(self) -> None:
        from meiko.plugins.builtin.saver import plan_links

        plan = plan_links("https://t.me/c/1234/100-103")
        assert plan.total == 4
        assert plan.targets[0].message_ids == [100, 101, 102, 103]

        listed = plan_links("t.me/chan/5,7,9")
        assert listed.targets[0].message_ids == [5, 7, 9]

        open_ended = plan_links("t.me/chan/50-")
        assert open_ended.targets[0].ranges[0].start == 50
        assert open_ended.total > 100

    def test_range_cap_and_deduplication(self) -> None:
        from meiko.plugins.builtin.saver import plan_links
        from meiko.plugins.builtin.saver.links import MAX_RANGE

        assert plan_links("t.me/c/1/1-99999").total == MAX_RANGE
        merged = plan_links("t.me/chan/10-12")
        assert merged.total == 3

    def test_forum_topic_links(self) -> None:
        from meiko.plugins.builtin.saver import parse_link

        target = parse_link("t.me/c/123456789/7/42")
        assert target is not None
        assert target.topic_id == 7
        assert target.message_ids == [42]

    def test_trailing_ranges_inherit_the_chat(self) -> None:
        from meiko.plugins.builtin.saver import plan_links

        plan = plan_links("t.me/chan/10 11-13")
        assert plan.total == 4
        assert plan.targets[0].message_ids == [10, 11, 12, 13]

    def test_invalid_input_is_reported_not_raised(self) -> None:
        from meiko.plugins.builtin.saver import plan_links

        plan = plan_links("hello world https://example.com/1")
        assert plan.ok is False
        assert len(plan.issues) == 3
        assert plan_links("").ok is False


# ------------------------------------------------------------ saver captions
class TestSaverCaptions:
    def test_placeholders_and_escaping(self) -> None:
        from meiko.plugins.builtin.saver import CaptionContext, render_caption

        text = render_caption(
            "{filename} {size} {date}",
            CaptionContext(filename="a<b>.mkv", size=1024 * 1024, date=datetime(2026, 1, 2, tzinfo=timezone.utc)),
        )
        assert "a&lt;b&gt;.mkv" in text
        assert "1.00 MB" in text
        assert "2026-01-02" in text

    def test_unknown_placeholder_is_left_visible(self) -> None:
        from meiko.plugins.builtin.saver import CaptionContext, render_caption

        assert "{nope}" in render_caption("{nope}", CaptionContext())

    def test_word_rules_replace_before_delete(self) -> None:
        from meiko.plugins.builtin.saver import WordRules

        rules = WordRules(delete=["x265"], replace=["Dual Audio=>Multi Audio"])
        assert rules.apply("Show Dual Audio x265 720p") == "Show Multi Audio 720p"
        assert rules.apply("nothing to change") == "nothing to change"

    def test_caption_validation(self) -> None:
        from meiko.plugins.builtin.saver import validate_caption

        assert validate_caption("{filename}")[0] is True
        assert validate_caption("static text")[0] is False
        assert validate_caption("")[0] is False
        assert validate_caption("x" * 1200)[0] is False

    def test_config_roundtrip_clamps_values(self) -> None:
        from meiko.plugins.builtin.saver import SaverConfig

        config = SaverConfig.from_dict(
            {"max_batch": 999, "send_mode": "bogus", "delay_seconds": -4, "delete_words": ["a"]}
        )
        assert config.max_batch == 50
        assert config.send_mode == "copy"
        assert config.delay_seconds == 0.0
        assert SaverConfig.from_dict(config.to_dict()).delete_words == ["a"]

    def test_needs_download_flag(self) -> None:
        from meiko.plugins.builtin.saver import SaverConfig

        assert SaverConfig().needs_download is False
        assert SaverConfig(use_caption=True).needs_download is True
        assert SaverConfig(delete_words=["x"]).needs_download is True
        assert SaverConfig(thumbnail_file_id="abc").needs_download is True


# ------------------------------------------------------------- saved index
class TestSavedIndex:
    def test_dedup_and_stats(self) -> None:
        from meiko.plugins.builtin.saver import SavedIndex, SavedPost

        index = SavedIndex()
        assert index.add(SavedPost(chat="-1001", message_id=1, unique_id="u1", size=2048)) is True
        assert index.add(SavedPost(chat="-1001", message_id=2, unique_id="u1")) is False
        assert index.is_duplicate("u1") is True
        assert index.stats()["count"] == 1
        assert index.stats()["bytes"] == 2048

    def test_persistence_roundtrip(self) -> None:
        from meiko.plugins.builtin.saver import SavedIndex, SavedPost

        index = SavedIndex()
        index.add(SavedPost(chat="-1001", message_id=1, unique_id="u1", filename="a.mkv"))
        restored = SavedIndex(index.to_list())
        assert restored.recent()[0].filename == "a.mkv"
        assert len(restored) == 1

    def test_limit_keeps_newest(self) -> None:
        from meiko.plugins.builtin.saver import SavedIndex, SavedPost

        index = SavedIndex(limit=2)
        for position in range(4):
            index.add(SavedPost(chat="-1", message_id=position, unique_id=f"u{position}"))
        assert [post.message_id for post in index.entries] == [3, 2]


# ---------------------------------------------------------------- renamer
class TestRenamerEngine:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Show.S02E05.1080p.WEB-DL.x265-GROUP.mkv", "Show S02E05 [1080p].mkv"),
            ("[SubsPlease] Anime S01E12 (720p) [ABCD].mkv", "Anime S01E12 [720p].mkv"),
        ],
    )
    def test_template_rendering(self, raw: str, expected: str) -> None:
        from meiko.plugins.builtin.renamer import plan_rename

        assert plan_rename(raw).renamed == expected

    def test_parse_extracts_fields(self) -> None:
        from meiko.plugins.builtin.renamer import parse_name

        parsed = parse_name("Some.Show.2019.S01E03.720p.WEB-DL.x264-GROUP.mp4")
        assert parsed.title.startswith("Some Show")
        assert (parsed.season, parsed.episode) == (1, 3)
        assert parsed.quality == "720p"
        assert parsed.year == "2019"
        assert parsed.group == "GROUP"
        assert parsed.extension == ".mp4"

    def test_episode_offset_and_truncation(self) -> None:
        from meiko.plugins.builtin.renamer import plan_rename

        plan = plan_rename("Show.S01E05.mkv", episode_offset=1)
        assert plan.renamed.startswith("Show S01E06")
        long_plan = plan_rename("Show.S01E05.mkv", template="{title}" * 40, max_length=40)
        assert len(long_plan.renamed) <= 40
        assert long_plan.warnings

    def test_invalid_template_falls_back_safely(self) -> None:
        from meiko.plugins.builtin.renamer import plan_rename

        plan = plan_rename("Show.S01E05.mkv", template="{title")
        assert plan.renamed.endswith(".mkv")
        assert plan.warnings

    def test_media_type_guessing(self) -> None:
        from meiko.plugins.builtin.renamer import guess_media_type

        assert guess_media_type("mkv") == "video"
        assert guess_media_type(".mp3") == "audio"
        assert guess_media_type("pdf") == "document"
        assert guess_media_type("zip") == "archive"
        assert guess_media_type("xyz") == "file"


# ------------------------------------------------------------------- music
def _fake_mp3(payload: bytes = b"\xff\xfb" + b"\x00" * 400) -> bytes:
    return payload


class TestMusic:
    def test_id3_roundtrip_is_idempotent(self) -> None:
        from meiko.plugins.builtin.music import TrackTags, read_tags, write_tag

        tags = TrackTags(title="Song", artist="Artist", album="LP", year="2024", track="3")
        tagged, result = write_tag(_fake_mp3(), tags)
        assert result.stripped_bytes == 0
        read_back = read_tags(tagged)
        assert read_back.title == "Song"
        assert read_back.artist == "Artist"
        assert read_back.track == "3"

        twice, second = write_tag(tagged, tags)
        assert len(twice) == len(tagged)
        assert second.stripped_bytes > 0

    def test_artwork_is_embedded(self) -> None:
        from meiko.plugins.builtin.music import TrackTags, write_tag

        artwork = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        tagged, result = write_tag(_fake_mp3(), TrackTags(title="x"), artwork)
        assert result.artwork_applied is True
        assert b"APIC" in tagged
        assert b"image/png" in tagged

    def test_filename_guessing(self) -> None:
        from meiko.plugins.builtin.music import guess_tags_from_filename

        tags = guess_tags_from_filename("03 - Arijit Singh - Kesariya [320kbps].mp3")
        assert (tags.title, tags.artist, tags.track) == ("Kesariya", "Arijit Singh", "3")
        plain = guess_tags_from_filename("Daft Punk - One More Time.mp3")
        assert (plain.artist, plain.title) == ("Daft Punk", "One More Time")

    def test_library_search_is_fuzzy_and_deduplicated(self) -> None:
        from meiko.plugins.builtin.music import LibraryEntry, MusicLibrary, guess_tags_from_filename

        library = MusicLibrary()
        entry = LibraryEntry.from_track(
            "03 - Arijit Singh - Kesariya.mp3", guess_tags_from_filename("03 - Arijit Singh - Kesariya.mp3")
        )
        assert library.add(entry) is True
        assert library.add(entry) is False
        hits = library.search("kesariya")
        assert hits and hits[0][0].filename.endswith("Kesariya.mp3")
        assert library.search("zzzz") == []
        assert library.stats()["tracks"] == 1
        assert MusicLibrary(library.to_list()).search("arijit")

    def test_cover_generation_produces_a_png(self) -> None:
        from meiko.plugins.builtin.music import placeholder_cover, render_cover_text
        from meiko.plugins.builtin.music import TrackTags

        data = placeholder_cover(render_cover_text(TrackTags(title="Song", artist="Artist")))
        assert data.startswith(b"\x89PNG")

    def test_tag_argument_parsing(self) -> None:
        from meiko.plugins.builtin.music.plugin import parse_tag_argument

        simple = parse_tag_argument("Arijit - Kesariya", "x.mp3")
        assert (simple.artist, simple.title) == ("Arijit", "Kesariya")
        keyed = parse_tag_argument("title=Believer artist=Imagine Dragons album=Evolve", "x.mp3")
        assert keyed.title == "Believer"
        assert keyed.artist == "Imagine Dragons"
        assert keyed.album == "Evolve"


# -------------------------------------------------------------- post maker
class TestPostMaker:
    def test_html_validation(self) -> None:
        from meiko.plugins.builtin.post_maker import validate_html

        assert validate_html("<b>ok</b> <blockquote>q</blockquote>").ok is True
        assert validate_html("<b>unclosed").ok is False
        assert validate_html("</b>stray").ok is False
        script = validate_html("<script>alert(1)</script>")
        assert script.ok is False
        assert any("not supported" in error for error in script.errors)

    def test_render_escapes_user_values(self) -> None:
        from meiko.plugins.builtin.post_maker import PostTemplate, render_post

        template = PostTemplate(body="<b>{title}</b>")
        assert render_post(template, {"title": "<img src=x>"}) == "<b>&lt;img src=x&gt;</b>"

    def test_buttons_parsing_and_rows(self) -> None:
        from meiko.plugins.builtin.post_maker import PostTemplate, parse_buttons

        buttons = parse_buttons("Join | https://t.me/x\nSite | https://example.com")
        assert [button.text for button in buttons] == ["Join", "Site"]
        assert parse_buttons("garbage line") == []
        assert len(PostTemplate(buttons=buttons).markup_rows()) == 1

    def test_preview_strips_tags(self) -> None:
        from meiko.plugins.builtin.post_maker import PostTemplate, preview_text, suggest_fill

        template = PostTemplate(body="<b>{title}</b>\n{description}")
        assert "<b>" not in preview_text(template, suggest_fill(template))
        assert "Example post title" in preview_text(template, suggest_fill(template))

    def test_schedule_parsing(self) -> None:
        from meiko.plugins.builtin.post_maker import parse_when

        reference = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        assert parse_when("+10m", reference) == reference + timedelta(minutes=10)
        assert parse_when("+2h", reference) == reference + timedelta(hours=2)
        assert parse_when("2026-02-03T09:30") == datetime(2026, 2, 3, 9, 30, tzinfo=timezone.utc)
        assert parse_when("tomorrow") is None

    def test_queue_due_and_cancel(self) -> None:
        from meiko.plugins.builtin.post_maker import PostQueue, ScheduledPost

        reference = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        queue = PostQueue()
        queue.add(ScheduledPost(id="a", chat_id=1, text="hi", send_at=reference.isoformat()))
        queue.add(
            ScheduledPost(id="b", chat_id=1, text="later", send_at=(reference + timedelta(hours=1)).isoformat())
        )
        assert [post.id for post in queue.due(reference)] == ["a"]
        assert queue.cancel("a") is True
        assert queue.cancel("zzz") is False
        assert len(queue.pending) == 1
        assert PostQueue(queue.to_list()).upcoming(5)[0].id == "b"


# -------------------------------------------------------------- keep alive
class TestKeepAlive:
    def test_alert_state_machine(self) -> None:
        from meiko.plugins.builtin.keep_alive import HeartbeatTracker

        tracker = HeartbeatTracker()
        assert tracker.record(120, True) is None
        assert tracker.record(0, False) is None
        assert tracker.record(0, False) is None
        assert tracker.record(0, False) == "down"
        assert tracker.record(0, False) is None  # no alert spam
        assert tracker.record(90, True) == "recovered"
        assert tracker.success_rate() == pytest.approx(33.3)
        assert tracker.state.consecutive_failures == 0

    def test_summary_and_serialisation(self) -> None:
        from meiko.plugins.builtin.keep_alive import HeartbeatTracker

        tracker = HeartbeatTracker()
        tracker.record(100, True)
        tracker.record(300, True)
        rows = dict(tracker.summary_rows())
        assert rows["Status"] == "healthy"
        assert rows["Pings"] == "2"
        assert "200 ms avg" in rows["Latency"]
        restored = HeartbeatTracker.from_dict(tracker.to_dict())
        assert restored.state.total_pings == 2
        assert len(restored.samples) == 2

    def test_history_is_capped(self) -> None:
        from meiko.plugins.builtin.keep_alive import HeartbeatTracker

        tracker = HeartbeatTracker(history=3)
        for _ in range(6):
            tracker.record(50, True)
        assert len(tracker.samples) == 3
        assert tracker.state.total_pings == 6

    def test_next_ping_due(self) -> None:
        from meiko.plugins.builtin.keep_alive import next_ping_due

        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        assert next_ping_due("", 300, now) is True
        assert next_ping_due((now - timedelta(seconds=10)).isoformat(), 300, now) is False
        assert next_ping_due((now - timedelta(seconds=400)).isoformat(), 300, now) is True

    def test_duration_formatting(self) -> None:
        from meiko.plugins.builtin.keep_alive import format_duration

        assert format_duration(45) == "45s"
        assert format_duration(125) == "2m 5s"
        assert format_duration(3 * 3600 + 60) == "3h 1m"
        assert format_duration(2 * 86400 + 3600) == "2d 1h"


# -------------------------------------------------------------- code runner
class TestCodeRunnerSandbox:
    async def test_python_runs_and_returns_output(self) -> None:
        from meiko.plugins.builtin.code_runner import RunLimits, run_snippet

        result = await run_snippet("print(sum(range(10)))", limits=RunLimits(timeout_seconds=10))
        assert result.ok is True, result.stderr
        assert result.stdout.strip() == "45"

    async def test_network_is_blocked_by_default(self) -> None:
        from meiko.plugins.builtin.code_runner import RunLimits, run_snippet

        code = "import socket\nprint(socket.create_connection(('1.1.1.1', 80)))"
        result = await run_snippet(code, limits=RunLimits(timeout_seconds=10))
        assert result.ok is False
        assert "block" in (result.stderr or "").lower() or "network" in (result.stderr or "").lower()

    async def test_infinite_loop_times_out(self) -> None:
        from meiko.plugins.builtin.code_runner import RunLimits, run_snippet

        result = await run_snippet("while True:\n    pass", limits=RunLimits(timeout_seconds=2))
        assert result.ok is False
        assert result.timed_out is True

    def test_hard_blocklist_rejects_dangerous_source(self) -> None:
        from meiko.plugins.builtin.code_runner.runtime import RunLimits, SnippetRejected, validate_source

        limits = RunLimits()
        with pytest.raises(SnippetRejected):
            validate_source("import shutil; shutil.rmtree('/')", limits)
        with pytest.raises(SnippetRejected):
            validate_source("DROP TABLE users;", limits)
        assert validate_source("print('fine')", limits) == "print('fine')"

    def test_languages_are_capped(self) -> None:
        from meiko.plugins.builtin.code_runner.runtime import ALLOWED_LANGUAGES

        assert set(ALLOWED_LANGUAGES) == {"python", "node", "bash", "sql"}


# -------------------------------------------------------------- file store
class TestFileStoreAndWorkspace:
    def test_workspace_quota_and_safe_names(self, tmp_path, monkeypatch) -> None:
        from meiko.plugins.support import FileTooLarge, QuotaExceeded, Workspace, safe_filename

        monkeypatch.setenv("MEIKO_DATA_DIR", str(tmp_path))
        workspace = Workspace("demo", scope=f"bot:{uuid.uuid4().hex[:8]}", quota_bytes=14, max_file_bytes=8)
        assert safe_filename("../../etc/passwd") == "passwd"
        stored = workspace.write_bytes("a.bin", b"1234")
        assert stored.size == 4
        with pytest.raises(FileTooLarge):
            workspace.write_bytes("b.bin", b"123456789")
        workspace.write_bytes("b.bin", b"12345678")  # exactly the file limit
        assert workspace.usage() == 12
        with pytest.raises(QuotaExceeded):  # 12 B used, 2 B free, 4 B asked
            workspace.write_bytes("c.bin", b"1234")
        assert workspace.stats()["files"] >= 1

    def test_human_size(self) -> None:
        from meiko.plugins.support import human_size

        assert human_size(0) == "0 B"
        assert human_size(1536) == "1.5 KB"


# --------------------------------------------------------------- toggles API
class TestPluginToggles:
    async def test_toggles_list_reports_state(self, app_client) -> None:
        from tests.test_api import VALID_TOKEN, auth_headers, dev_login

        token, _ = await dev_login(app_client, 6101)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()["id"]
        await app_client.post(
            f"/api/v1/plugins/welcome/toggle?bot_id={bot_id}&enabled=true", headers=auth_headers(token)
        )
        listing = await app_client.get(
            f"/api/v1/plugins/toggles?bot_id={bot_id}", headers=auth_headers(token)
        )
        assert listing.status_code == 200
        rows = {row["slug"]: row for row in listing.json()}
        assert {"saver", "renamer", "music", "post_maker", "keep_alive"} <= set(rows)
        assert rows["welcome"]["enabled"] is True
        assert rows["saver"]["enabled"] is False

    async def test_toggle_installs_then_enables_and_disables(self, app_client) -> None:
        from tests.test_api import VALID_TOKEN, auth_headers, dev_login

        token, _ = await dev_login(app_client, 6102)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()["id"]
        enabled = await app_client.post(
            f"/api/v1/plugins/renamer/toggle?bot_id={bot_id}&enabled=true", headers=auth_headers(token)
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["enabled"] is True

        disabled = await app_client.post(
            f"/api/v1/plugins/renamer/toggle?bot_id={bot_id}&enabled=false", headers=auth_headers(token)
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False

    async def test_toggle_requires_config_for_ai(self, app_client) -> None:
        from tests.test_api import VALID_TOKEN, auth_headers, dev_login

        token, _ = await dev_login(app_client, 6103)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(token))
        ).json()["id"]
        response = await app_client.post(
            f"/api/v1/plugins/ai/toggle?bot_id={bot_id}&enabled=true", headers=auth_headers(token)
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "config_required"

    async def test_toggle_unknown_plugin_is_404(self, app_client) -> None:
        from tests.test_api import auth_headers, dev_login

        token, _ = await dev_login(app_client, 6104)
        response = await app_client.post(
            "/api/v1/plugins/not_a_plugin/toggle?enabled=true", headers=auth_headers(token)
        )
        assert response.status_code == 404

    async def test_other_users_bot_is_not_toggleable(self, app_client) -> None:
        from tests.test_api import VALID_TOKEN, auth_headers, dev_login

        owner, _ = await dev_login(app_client, 6105)
        stranger, _ = await dev_login(app_client, 6106)
        bot_id = (
            await app_client.post("/api/v1/bots", json={"token": VALID_TOKEN}, headers=auth_headers(owner))
        ).json()["id"]
        response = await app_client.post(
            f"/api/v1/plugins/welcome/toggle?bot_id={bot_id}&enabled=true",
            headers=auth_headers(stranger),
        )
        assert response.status_code == 404


# ---------------------------------------------------------- capabilities API
class TestCapabilities:
    async def _owner_token(self) -> str:
        from meiko.api.auth import AuthService
        from meiko.db.base import session_scope
        from meiko.services.core import UserService

        async with session_scope() as session:
            owner = await UserService(session).get_or_create_telegram_user(7201, username="cap_owner")
            owner.is_owner = True
            owner.role = "owner"
            await session.flush()
            token = await AuthService(session).create_session(owner)
        return token

    async def test_capability_closed_by_default(self, app_client) -> None:
        from tests.test_api import auth_headers

        token = await self._owner_token()
        response = await app_client.get("/api/v1/owner/capabilities", headers=auth_headers(token))
        assert response.status_code == 200
        rows = {row["capability"]: row for row in response.json()}
        assert set(rows) == {"code_exec", "remote_fetch", "file_upload"}
        assert rows["code_exec"]["enabled"] is False

    async def test_owner_can_open_and_narrow_a_capability(self, app_client) -> None:
        from tests.test_api import auth_headers

        token = await self._owner_token()
        opened = await app_client.post(
            "/api/v1/owner/capabilities/code_exec?enabled=true&allowed_bot_ids=5&max_runs_per_day=10",
            headers=auth_headers(token),
        )
        assert opened.status_code == 200, opened.text
        body = opened.json()
        assert body["enabled"] is True
        assert body["allowed_bot_ids"] == [5]
        assert body["max_runs_per_day"] == 10

        closed = await app_client.post(
            "/api/v1/owner/capabilities/code_exec?enabled=false", headers=auth_headers(token)
        )
        assert closed.json()["enabled"] is False

    async def test_unknown_capability_is_404(self, app_client) -> None:
        from tests.test_api import auth_headers

        token = await self._owner_token()
        response = await app_client.post(
            "/api/v1/owner/capabilities/not_real?enabled=true", headers=auth_headers(token)
        )
        assert response.status_code == 404

    async def test_non_owner_cannot_change_capabilities(self, app_client) -> None:
        from tests.test_api import auth_headers, dev_login

        token, _ = await dev_login(app_client, 7202)
        response = await app_client.post(
            "/api/v1/owner/capabilities/code_exec?enabled=true", headers=auth_headers(token)
        )
        assert response.status_code == 403

    async def test_gate_blocks_when_disabled(self) -> None:
        from meiko.plugins.support import CapabilityGate, GateDenied

        gate = CapabilityGate("code_exec")
        await gate.set_state(enabled=False)
        with pytest.raises(GateDenied):
            await gate.require(telegram_id=1, bot_id=1, platform_owner=True, owner_telegram_id=1)

        await gate.set_state(enabled=True, allowed_telegram_ids=[42], allowed_bot_ids=[7])
        with pytest.raises(GateDenied):
            await gate.require(telegram_id=42, bot_id=8, platform_owner=True)
        await gate.require(telegram_id=42, bot_id=7, platform_owner=True)
