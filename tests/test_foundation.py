"""Configuration, crypto and cache tests."""

from __future__ import annotations

import pytest

from meiko.security.crypto import (
    decrypt_secret,
    encrypt_secret,
    hash_api_key,
    hash_password,
    mask_secret,
    sign_session,
    token_fingerprint,
    verify_password,
    verify_session,
)


class TestSettings:
    def test_sqlite_fallback_when_no_database_url(self, settings):
        assert settings.resolved_database_url.startswith("sqlite+aiosqlite")

    def test_owner_ids_parsed_from_csv(self, monkeypatch, settings):
        from meiko.config import Settings

        cfg = Settings(owner_telegram_ids="1, 2 ,x;3")
        assert cfg.owner_ids == {1, 2, 3}

    def test_cors_origins_split(self, settings):
        from meiko.config import Settings

        cfg = Settings(cors_origins="http://a.test, http://b.test")
        assert cfg.cors_origin_list == ["http://a.test", "http://b.test"]

    def test_directories_created(self, settings):
        assert (settings.data_dir / "logs").exists()
        assert (settings.data_dir / "locks").exists()


class TestCrypto:
    def test_token_roundtrip(self, settings):
        token = "123456:AAFakeTokenValueForTestsOnly"
        stored = encrypt_secret(token)
        assert stored.startswith("enc:v1:")
        assert token not in stored
        assert decrypt_secret(stored) == token

    def test_encryption_is_nondeterministic(self, settings):
        assert encrypt_secret("abc") != encrypt_secret("abc")

    def test_mask_secret_hides_value(self, settings):
        masked = mask_secret("123456:AAFakeToken")
        assert masked.startswith("1234")
        assert "****" in masked
        assert "AAFake" not in masked

    def test_fingerprint_is_stable_and_not_reversible(self, settings):
        assert token_fingerprint("abc") == token_fingerprint("abc")
        assert token_fingerprint("abc") != token_fingerprint("abd")
        assert len(token_fingerprint("abc")) == 16

    def test_password_hashing(self, settings):
        stored = hash_password("s3cret!")
        assert verify_password("s3cret!", stored)
        assert not verify_password("wrong", stored)

    def test_session_signing_and_tamper_detection(self, settings):
        token = sign_session({"uid": 5, "role": "user"}, 60)
        payload = verify_session(token)
        assert payload and payload["uid"] == 5
        b64, sig = token.split(".")
        tampered = b64[:-2] + "aa." + sig
        assert verify_session(tampered) is None

    def test_expired_session_rejected(self, settings):
        token = sign_session({"uid": 5}, -10)
        assert verify_session(token) is None

    def test_api_key_hash_matches_helper(self, settings):
        from meiko.security.crypto import new_api_key

        plaintext, stored = new_api_key()
        assert hash_api_key(plaintext) == stored


class TestCache:
    async def test_set_get_delete(self, cache):
        await cache.set("k", {"a": 1}, ttl=30)
        assert await cache.get("k") == {"a": 1}
        await cache.delete("k")
        assert await cache.get("k") is None

    async def test_ttl_expiry(self, cache):
        await cache.set("temp", "v", ttl=-1)
        assert await cache.get("temp") is None

    async def test_rate_limit_blocks_after_quota(self, cache):
        allowed = [await cache.rate_limit("user:1", limit=3, window=60) for _ in range(4)]
        assert [a[0] for a in allowed] == [True, True, True, False]

    async def test_queue_push_pop(self, cache):
        await cache.push("jobs", {"id": 1})
        assert await cache.queue_size("jobs") == 1
        assert await cache.pop("jobs") == {"id": 1}

    async def test_lock_is_exclusive(self, cache):
        async with cache.lock("worker", timeout=1) as first:
            assert first is True
            async with cache.lock("worker", timeout=0.05) as second:
                assert second is False

    async def test_ping(self, cache):
        assert (await cache.ping())["status"] == "ok"
