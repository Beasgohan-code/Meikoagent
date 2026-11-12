"""One URL policy for every module that accepts a link from a stranger.

These are the cases the upstream bots got wrong or solved five different ways:
``file://`` paths, ``localhost`` fetches, credentials in a URL, and the cloud
metadata address. The policy is tested directly *and* through each consumer, so
"validated" means "validated where the bytes move", not "validated in a helper
nobody calls".
"""

from __future__ import annotations

import pytest

from meiko.modules import urls
from meiko.modules.leech import HTTPDownloader, LocalDownloader, TransferEngine, TransferError
from meiko.modules.linkshare import LinkError, LinkshareService
from meiko.modules.store import ModuleStore
from meiko.plugins.builtin.music.sources import MediaResolver, PlayRequest, check_play_url

MALICIOUS = [
    "http://127.0.0.1:8000/api/v1/bots",  # loopback
    "http://localhost:5432/",  # loopback by name
    "http://169.254.169.254/latest/meta-data/iam/",  # cloud metadata
    "http://10.1.2.3/internal",  # private v4
    "http://192.168.0.1/router",  # private v4
    "http://172.16.5.4/",  # private v4
    "https://[::1]/",  # loopback v6
    "https://[fd00::1]/",  # unique-local v6
    "http://0.0.0.0/",  # unspecified
    "file:///etc/passwd",  # wrong scheme
    "gopher://example.com/",  # wrong scheme
    "ftp://example.com/x",  # wrong scheme
    "https://user:secret@example.com/",  # credentials
    "https://printer.local/",  # mDNS
    "https://db.internal/",  # internal suffix
    "https://t.me/somechannel/12",  # telegram, resolved by the bot
    "https://example.com:99999/",  # invalid port
    "https://",  # no host
    "",
]


class TestPolicy:
    @pytest.mark.parametrize("url", MALICIOUS)
    def test_refused(self, url: str):
        assert urls.is_allowed(url) is False
        assert urls.reason(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/song.mp3",
            "http://example.com",
            "https://cdn.example.co.uk/a/b/c.mkv?token=1",
            "https://8.8.8.8/x",
            "https://[2001:4860:4860::8888]/x",
        ],
    )
    def test_allowed(self, url: str):
        assert urls.is_allowed(url) is True
        assert urls.check(url) == url.strip()

    def test_telegram_links_are_allowed_only_when_the_bot_resolves_them(self):
        assert urls.is_allowed("https://t.me/c/123/45") is False
        assert urls.is_allowed("https://t.me/c/123/45", allow_telegram=True) is True
        assert check_play_url("https://t.me/c/123/45") == "https://t.me/c/123/45"

    def test_control_characters_and_length_are_refused(self):
        assert urls.is_allowed("https://example.com/\r\nHost: evil") is False
        assert urls.is_allowed("https://example.com/" + "a" * 3000) is False

    def test_whitespace_is_trimmed_rather_than_rejected(self):
        assert urls.check("  https://example.com/x  ") == "https://example.com/x"

    def test_canonical_form_drops_case_and_empty_path(self):
        assert urls.canonical("HTTPS://Example.COM") == "https://example.com/"
        assert urls.canonical("https://Example.com:8443/a") == "https://example.com:8443/a"

    def test_describe_is_what_the_console_shows(self):
        good = urls.describe("https://example.com/a")
        assert good == {
            "url": "https://example.com/a",
            "scheme": "https",
            "host": "example.com",
            "port": None,
            "allowed": True,
            "reason": "",
        }
        bad = urls.describe("http://127.0.0.1/")
        assert bad["allowed"] is False and "private" in bad["reason"]

    def test_rejection_never_echoes_credentials_or_query_tokens(self):
        url = "https://user:secretpassword@example.com/file?token=abc123"
        message = urls.reason(url)
        assert "secretpassword" not in message and "abc123" not in message
        assert "example.com" in message, "the host is still useful for debugging"
        assert len(urls.redact("https://e.com/" + "a" * 500)) <= 101


class TestLinkshareUsesIt:
    """A share link is a promise to fetch that URL later, so creation is the gate."""

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/x", "https://a:b@example.com/"])
    def test_create_refuses_bad_targets(self, tmp_path, url: str):
        service = LinkshareService(ModuleStore(tmp_path / "modules"))
        with pytest.raises(LinkError, match="only http|private|credentials"):
            service.create("1", url=url, owner_id=1)

    def test_a_public_url_is_accepted_and_stored_verbatim(self, tmp_path):
        service = LinkshareService(ModuleStore(tmp_path / "modules"))
        link = service.create("9", url="https://example.com/files/1.zip", owner_id=7, ttl_hours=1)
        assert link.url == "https://example.com/files/1.zip"

    def test_a_batch_is_refused_once_any_target_is_bad(self, tmp_path):
        service = LinkshareService(ModuleStore(tmp_path / "modules"))
        with pytest.raises(LinkError):
            service.create_batch(
                "11", urls=["https://example.com/ok.zip", "http://169.254.169.254/x"], owner_id=7
            )
        created = service.list_links("11", owner_id=7)
        assert created == [], "a refused batch must not leave half its links behind"


class TestLeechUsesIt:
    @pytest.mark.parametrize("url", ["http://127.0.0.1:8888/x", "file:///etc/shadow", "http://10.0.0.1/"])
    @pytest.mark.asyncio
    async def test_http_downloader_validates_before_connecting(self, url: str, tmp_path):
        downloader = HTTPDownloader()
        with pytest.raises(TransferError) as caught:
            await downloader.validate(url)
        # non-retryable: the same URL will never become acceptable
        assert caught.value.retryable is False

    @pytest.mark.asyncio
    async def test_the_engine_never_writes_for_a_refused_url(self, tmp_path):
        target = tmp_path / "nope.bin"
        engine = TransferEngine([LocalDownloader(), HTTPDownloader()])
        with pytest.raises(TransferError):
            await engine.fetch("http://169.254.169.254/latest/meta-data/", target)
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_local_paths_still_work(self, tmp_path):
        source = tmp_path / "ok.bin"
        source.write_bytes(b"data")
        result = await TransferEngine([LocalDownloader()]).fetch(str(source), tmp_path / "copy.bin")
        assert result.ok and (tmp_path / "copy.bin").read_bytes() == b"data"


class TestMusicUsesIt:
    @pytest.mark.asyncio
    async def test_a_private_url_is_never_queued(self):
        outcome = await MediaResolver().resolve(PlayRequest(url="http://127.0.0.1/stream.mp3"))
        assert outcome.ok is False
        assert "private" in outcome.reason

    @pytest.mark.asyncio
    async def test_a_public_media_url_is_still_queued(self):
        outcome = await MediaResolver().resolve(PlayRequest(url="https://cdn.example.com/a.mp3"))
        assert outcome.first is not None and outcome.first.stream_url.endswith("a.mp3")

    @pytest.mark.asyncio
    async def test_a_telegram_link_is_handed_to_the_saver_not_fetched(self):
        outcome = await MediaResolver().resolve(PlayRequest(url="https://t.me/c/1/2"))
        assert outcome.layer == "link" and "/save" in outcome.reason


def test_the_api_exposes_the_same_answer():
    """The console checks a URL with the same function the modules use."""
    from meiko.api.app import create_app

    paths = create_app().openapi()["paths"]
    assert "/api/v1/linkshare/validate" in paths
