import pytest

import security.webhook_event_deduplication as dedupe


@pytest.fixture(autouse=True)
def patch_redis(monkeypatch, fake_redis):
    monkeypatch.setattr(dedupe, "redis_client", fake_redis)
    return fake_redis


def test_acquire_event_claim_returns_processed_when_marker_exists(fake_redis):
    fake_redis.setex(dedupe.event_key("evt-processed"), 86400, dedupe.EVENT_PROCESSED_VALUE)

    status, token = dedupe.acquire_event_claim("evt-processed")

    assert status == dedupe.EVENT_CLAIM_PROCESSED
    assert token is None
    assert fake_redis.exists(dedupe.event_lock_key("evt-processed")) == 0


def test_acquire_event_claim_creates_lock_with_token_and_ttl(fake_redis):
    status, token = dedupe.acquire_event_claim("evt-new")

    assert status == dedupe.EVENT_CLAIM_ACQUIRED
    assert token
    assert fake_redis.get(dedupe.event_lock_key("evt-new")) == token
    assert fake_redis.ttls[dedupe.event_lock_key("evt-new")] == dedupe.EVENT_LOCK_TTL_SECONDS


def test_acquire_event_claim_returns_processing_when_lock_is_occupied(fake_redis):
    fake_redis.set(dedupe.event_lock_key("evt-locked"), "other-token", nx=True, ex=60)

    status, token = dedupe.acquire_event_claim("evt-locked")

    assert status == dedupe.EVENT_CLAIM_PROCESSING
    assert token is None
    assert fake_redis.get(dedupe.event_lock_key("evt-locked")) == "other-token"


def test_acquire_event_claim_returns_processed_when_marker_appears_after_lock_contention(fake_redis):
    event_id = "evt-processed-after-contention"
    fake_redis.set(dedupe.event_lock_key(event_id), "other-token", nx=True, ex=60)
    fake_redis.setex(dedupe.event_key(event_id), 86400, dedupe.EVENT_PROCESSED_VALUE)

    status, token = dedupe.acquire_event_claim(event_id)

    assert status == dedupe.EVENT_CLAIM_PROCESSED
    assert token is None


def test_acquire_event_claim_retries_once_when_lock_disappears(monkeypatch, fake_redis):
    event_id = "evt-lock-disappears"
    original_set = fake_redis.set
    calls = {"count": 0}

    def flaky_set(key, value, nx=False, ex=None):
        if key == dedupe.event_lock_key(event_id):
            calls["count"] += 1
            if calls["count"] == 1:
                return False
        return original_set(key, value, nx=nx, ex=ex)

    monkeypatch.setattr(fake_redis, "set", flaky_set)

    status, token = dedupe.acquire_event_claim(event_id)

    assert status == dedupe.EVENT_CLAIM_ACQUIRED
    assert token
    assert calls["count"] == 2
    assert fake_redis.get(dedupe.event_lock_key(event_id)) == token


def test_release_event_claim_removes_lock_only_for_owner(fake_redis):
    event_id = "evt-release-owner"
    fake_redis.set(dedupe.event_lock_key(event_id), "owner-token", nx=True, ex=60)

    dedupe.release_event_claim(event_id, "owner-token")

    assert fake_redis.exists(dedupe.event_lock_key(event_id)) == 0


def test_release_event_claim_does_not_remove_lock_for_non_owner(fake_redis):
    event_id = "evt-release-non-owner"
    fake_redis.set(dedupe.event_lock_key(event_id), "owner-token", nx=True, ex=60)

    dedupe.release_event_claim(event_id, "wrong-token")

    assert fake_redis.get(dedupe.event_lock_key(event_id)) == "owner-token"


def test_expired_lock_allows_new_claim(fake_redis):
    event_id = "evt-expired-lock"
    fake_redis.set(dedupe.event_lock_key(event_id), "old-token", nx=True, ex=60)

    fake_redis.advance_time(61)
    status, token = dedupe.acquire_event_claim(event_id)

    assert status == dedupe.EVENT_CLAIM_ACQUIRED
    assert token
    assert fake_redis.get(dedupe.event_lock_key(event_id)) == token


def test_event_keys_are_independent(fake_redis):
    first_status, first_token = dedupe.acquire_event_claim("evt-a")
    second_status, second_token = dedupe.acquire_event_claim("evt-b")

    assert first_status == dedupe.EVENT_CLAIM_ACQUIRED
    assert second_status == dedupe.EVENT_CLAIM_ACQUIRED
    assert first_token != second_token
    assert fake_redis.get(dedupe.event_lock_key("evt-a")) == first_token
    assert fake_redis.get(dedupe.event_lock_key("evt-b")) == second_token


def test_mark_event_processed_preserves_value_and_ttl(fake_redis):
    event_id = "evt-mark-processed"
    status, token = dedupe.acquire_event_claim(event_id)
    assert status == dedupe.EVENT_CLAIM_ACQUIRED

    mark_status = dedupe.mark_event_processed(event_id, token)

    assert mark_status == dedupe.EVENT_CLAIM_PROCESSED
    assert fake_redis.get(dedupe.event_key(event_id)) == dedupe.EVENT_PROCESSED_VALUE
    assert fake_redis.ttls[dedupe.event_key(event_id)] == dedupe.EVENT_PROCESSED_TTL_SECONDS


def test_mark_event_processed_does_not_write_when_token_is_not_owner(fake_redis):
    event_id = "evt-lost-ownership"
    fake_redis.set(dedupe.event_lock_key(event_id), "owner-token", nx=True, ex=60)

    mark_status = dedupe.mark_event_processed(event_id, "wrong-token")

    assert mark_status == dedupe.EVENT_CLAIM_LOST_OWNERSHIP
    assert fake_redis.exists(dedupe.event_key(event_id)) == 0
    assert fake_redis.get(dedupe.event_lock_key(event_id)) == "owner-token"


def test_old_owner_does_not_remove_new_lock_after_expiration(fake_redis):
    event_id = "evt-old-owner"
    fake_redis.set(dedupe.event_lock_key(event_id), "old-token", nx=True, ex=60)
    fake_redis.advance_time(61)
    fake_redis.set(dedupe.event_lock_key(event_id), "new-token", nx=True, ex=60)

    dedupe.release_event_claim(event_id, "old-token")

    assert fake_redis.get(dedupe.event_lock_key(event_id)) == "new-token"


def test_acquire_event_claim_returns_unavailable_on_redis_failure(monkeypatch, fake_redis):
    def failing_exists(key):
        raise RuntimeError("Redis unavailable")

    monkeypatch.setattr(fake_redis, "exists", failing_exists)

    status, token = dedupe.acquire_event_claim("evt-redis-failure")

    assert status == dedupe.EVENT_CLAIM_UNAVAILABLE
    assert token is None


def test_mark_event_processed_returns_unavailable_on_redis_failure(monkeypatch, fake_redis):
    event_id = "evt-mark-redis-failure"
    fake_redis.set(dedupe.event_lock_key(event_id), "owner-token", nx=True, ex=60)

    def failing_setex(key, ttl, value):
        raise RuntimeError("Redis unavailable")

    monkeypatch.setattr(fake_redis, "setex", failing_setex)

    status = dedupe.mark_event_processed(event_id, "owner-token")

    assert status == dedupe.EVENT_CLAIM_UNAVAILABLE
