import json
import threading
import pytest
from flask import Flask, jsonify

from security.idempotency import _release_lock_if_owner, idempotent


def _register_idempotent_route(
    app,
    body,
    status_code,
    rule="/idempotent-test",
    endpoint="idempotent_test",
):
    calls = {"count": 0}

    def view():
        calls["count"] += 1
        return jsonify(body), status_code

    app.add_url_rule(
        rule,
        endpoint=endpoint,
        view_func=idempotent(ttl=300)(view),
        methods=["POST"],
    )
    return calls


def _register_sequential_idempotent_route(
    app,
    responses,
    rule="/idempotent-sequential-test",
    endpoint="idempotent_sequential_test",
):
    calls = {"count": 0}

    def view():
        index = min(calls["count"], len(responses) - 1)
        calls["count"] += 1
        body, status_code = responses[index]
        return jsonify(body), status_code

    app.add_url_rule(
        rule,
        endpoint=endpoint,
        view_func=idempotent(ttl=300)(view),
        methods=["POST"],
    )
    return calls


def _assert_cached_response_envelope(cached, body, status_code):
    assert cached["body"] == body
    assert cached["status_code"] == status_code
    assert isinstance(cached["request_fingerprint"], str)
    assert cached["request_fingerprint"].startswith("sha256:v1:")
    digest = cached["request_fingerprint"][len("sha256:v1:"):]
    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)


@pytest.fixture
def app(monkeypatch, fake_redis):
    app = Flask(__name__)
    app.config["TESTING"] = True
    monkeypatch.setattr("security.idempotency.redis_client", fake_redis)
    app.fake_redis = fake_redis
    return app


@pytest.mark.parametrize(
    ("body", "status_code"),
    [
        ({"error": "Invalid value"}, 400),
        ({"error": "Charge not found"}, 404),
        ({"message": "Payment confirmed"}, 200),
    ],
)
def test_replay_preserves_cached_body_and_status(app, body, status_code):
    calls = _register_idempotent_route(app, body, status_code)
    client = app.test_client()
    headers = {"Idempotency-Key": f"replay-{status_code}"}

    first_response = client.post("/idempotent-test", data=b'{"a":1}', headers=headers)
    replay_response = client.post("/idempotent-test", data=b'{"a":1}', headers=headers)

    assert first_response.status_code == status_code
    assert first_response.get_json() == body
    assert replay_response.status_code == status_code
    assert replay_response.get_json() == body
    assert calls["count"] == 1

    cached = json.loads(app.fake_redis.store[f"idempotency:replay-{status_code}"])
    _assert_cached_response_envelope(cached, body, status_code)


@pytest.mark.parametrize("server_error_status", [500, 503])
def test_server_error_response_is_not_cached_and_retry_can_store_success(
    app,
    server_error_status,
):
    calls = _register_sequential_idempotent_route(
        app,
        [
            ({"error": "Temporary failure"}, server_error_status),
            ({"message": "Recovered"}, 200),
        ],
    )
    client = app.test_client()
    key = f"server-error-then-success-{server_error_status}"
    headers = {"Idempotency-Key": key}
    redis_key = f"idempotency:{key}"

    first_response = client.post(
        "/idempotent-sequential-test",
        data=b'{"a":1}',
        headers=headers,
    )

    assert first_response.status_code == server_error_status
    assert first_response.get_json() == {"error": "Temporary failure"}
    assert calls["count"] == 1
    assert redis_key not in app.fake_redis.store

    second_response = client.post(
        "/idempotent-sequential-test",
        data=b'{"a":1}',
        headers=headers,
    )

    assert second_response.status_code == 200
    assert second_response.get_json() == {"message": "Recovered"}
    assert calls["count"] == 2
    cached = json.loads(app.fake_redis.store[redis_key])
    _assert_cached_response_envelope(cached, {"message": "Recovered"}, 200)

    third_response = client.post(
        "/idempotent-sequential-test",
        data=b'{"a":1}',
        headers=headers,
    )

    assert third_response.status_code == 200
    assert third_response.get_json() == {"message": "Recovered"}
    assert calls["count"] == 2


def test_same_key_with_different_body_returns_409_without_executing_view(app):
    calls = _register_idempotent_route(app, {"message": "Created"}, 201)
    client = app.test_client()
    headers = {"Idempotency-Key": "same-key-different-body"}

    first_response = client.post("/idempotent-test", data=b'{"a":1}', headers=headers)
    conflict_response = client.post(
        "/idempotent-test",
        data=b'{"a":2}',
        headers=headers,
    )

    assert first_response.status_code == 201
    assert conflict_response.status_code == 409
    assert conflict_response.get_json() == {
        "error": "Idempotency-Key reused with different request"
    }
    assert calls["count"] == 1


def test_same_key_with_different_body_does_not_overwrite_cached_response(app):
    calls = _register_idempotent_route(app, {"message": "Created"}, 201)
    client = app.test_client()
    key = "same-key-different-body-cache-preserved"
    headers = {"Idempotency-Key": key}
    redis_key = f"idempotency:{key}"

    first_response = client.post("/idempotent-test", data=b'{"a":1}', headers=headers)
    original_cached = app.fake_redis.store[redis_key]
    conflict_response = client.post(
        "/idempotent-test",
        data=b'{"a":2}',
        headers=headers,
    )

    assert first_response.status_code == 201
    assert conflict_response.status_code == 409
    assert conflict_response.get_json() == {
        "error": "Idempotency-Key reused with different request"
    }
    assert calls["count"] == 1
    assert app.fake_redis.store[redis_key] == original_cached


def test_same_key_with_different_query_string_returns_409(app):
    calls = _register_idempotent_route(app, {"message": "Created"}, 201)
    client = app.test_client()
    headers = {"Idempotency-Key": "same-key-different-query"}

    first_response = client.post(
        "/idempotent-test?source=a",
        data=b'{"a":1}',
        headers=headers,
    )
    conflict_response = client.post(
        "/idempotent-test?source=b",
        data=b'{"a":1}',
        headers=headers,
    )

    assert first_response.status_code == 201
    assert conflict_response.status_code == 409
    assert conflict_response.get_json() == {
        "error": "Idempotency-Key reused with different request"
    }
    assert calls["count"] == 1


def test_same_key_with_different_path_returns_409(app):
    first_calls = _register_idempotent_route(
        app,
        {"message": "First route"},
        201,
        rule="/idempotent-test",
        endpoint="idempotent_test",
    )
    second_calls = _register_idempotent_route(
        app,
        {"message": "Second route"},
        202,
        rule="/idempotent-test-other",
        endpoint="idempotent_test_other",
    )
    client = app.test_client()
    headers = {"Idempotency-Key": "same-key-different-path"}

    first_response = client.post("/idempotent-test", data=b'{"a":1}', headers=headers)
    conflict_response = client.post(
        "/idempotent-test-other",
        data=b'{"a":1}',
        headers=headers,
    )

    assert first_response.status_code == 201
    assert conflict_response.status_code == 409
    assert conflict_response.get_json() == {
        "error": "Idempotency-Key reused with different request"
    }
    assert first_calls["count"] == 1
    assert second_calls["count"] == 0


def test_semantically_equal_json_with_different_formatting_returns_409(app):
    calls = _register_idempotent_route(app, {"message": "Created"}, 201)
    client = app.test_client()
    headers = {"Idempotency-Key": "same-key-different-json-formatting"}

    first_response = client.post("/idempotent-test", data=b'{"a":1}', headers=headers)
    conflict_response = client.post(
        "/idempotent-test",
        data=b'{ "a": 1 }',
        headers=headers,
    )

    assert first_response.status_code == 201
    assert conflict_response.status_code == 409
    assert conflict_response.get_json() == {
        "error": "Idempotency-Key reused with different request"
    }
    assert calls["count"] == 1


def test_legacy_envelope_without_fingerprint_replays_without_executing_view(app):
    body = {"message": "Legacy envelope response"}
    app.fake_redis.store["idempotency:legacy-envelope-key"] = json.dumps(
        {"body": body, "status_code": 201}
    )
    calls = _register_idempotent_route(app, {"message": "New response"}, 202)

    response = app.test_client().post(
        "/idempotent-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": "legacy-envelope-key"},
    )

    assert response.status_code == 201
    assert response.get_json() == body
    assert calls["count"] == 0


def test_cached_400_response_is_replayed_without_executing_view(app):
    body = {"error": "Invalid value"}
    calls = _register_idempotent_route(app, body, 400)
    client = app.test_client()
    key = "cached-400-response"
    headers = {"Idempotency-Key": key}

    first_response = client.post("/idempotent-test", data=b'{"a":1}', headers=headers)
    replay_response = client.post("/idempotent-test", data=b'{"a":1}', headers=headers)

    assert first_response.status_code == 400
    assert first_response.get_json() == body
    assert replay_response.status_code == 400
    assert replay_response.get_json() == body
    assert calls["count"] == 1
    cached = json.loads(app.fake_redis.store[f"idempotency:{key}"])
    _assert_cached_response_envelope(cached, body, 400)


@pytest.mark.parametrize(
    "invalid_fingerprint",
    [
        None,
        "",
        "abc",
        123,
        "sha256:v1:",
        "sha256:v1:" + "g" * 64,
        "sha256:v1:" + "a" * 63,
        "sha256:v1:" + "a" * 65,
    ],
)
def test_invalid_cached_fingerprint_is_a_miss_and_is_overwritten(
    app,
    invalid_fingerprint,
):
    key = f"invalid-fingerprint-{invalid_fingerprint}"
    body = {"message": "Fresh response"}
    app.fake_redis.store[f"idempotency:{key}"] = json.dumps({
        "body": {"error": "Stale response"},
        "status_code": 201,
        "request_fingerprint": invalid_fingerprint,
    })
    calls = _register_idempotent_route(app, body, 202)

    response = app.test_client().post(
        "/idempotent-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 202
    assert response.status_code != 409
    assert response.get_json() == body
    assert calls["count"] == 1
    cached = json.loads(app.fake_redis.store[f"idempotency:{key}"])
    _assert_cached_response_envelope(cached, body, 202)


def test_invalid_cache_is_preserved_when_retry_returns_server_error(app):
    key = "invalid-cache-then-server-error"
    redis_key = f"idempotency:{key}"
    invalid_cache = "{not-json"
    app.fake_redis.store[redis_key] = invalid_cache
    calls = _register_idempotent_route(app, {"error": "Temporary failure"}, 503)

    response = app.test_client().post(
        "/idempotent-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 503
    assert response.get_json() == {"error": "Temporary failure"}
    assert calls["count"] == 1
    assert app.fake_redis.store[redis_key] == invalid_cache


def test_legacy_cached_body_replays_as_200_without_executing_view(app):
    body = {"message": "Legacy response"}
    app.fake_redis.store["idempotency:legacy-key"] = json.dumps(body)
    calls = _register_idempotent_route(app, {"message": "New response"}, 201)

    response = app.test_client().post(
        "/idempotent-test",
        headers={"Idempotency-Key": "legacy-key"},
    )

    assert response.status_code == 200
    assert response.get_json() == body
    assert calls["count"] == 0


def test_corrupted_json_cache_is_a_miss_and_is_overwritten(app):
    key = "corrupted-key"
    body = {"message": "Fresh response"}
    app.fake_redis.store[f"idempotency:{key}"] = "{not-json"
    calls = _register_idempotent_route(app, body, 201)

    response = app.test_client().post(
        "/idempotent-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 201
    assert response.get_json() == body
    assert calls["count"] == 1
    cached = json.loads(app.fake_redis.store[f"idempotency:{key}"])
    _assert_cached_response_envelope(cached, body, 201)


@pytest.mark.parametrize("invalid_status", ["400", True, 99, 600])
def test_invalid_cached_status_is_a_miss_and_is_overwritten(app, invalid_status):
    key = f"invalid-status-{invalid_status}"
    body = {"message": "Fresh response"}
    app.fake_redis.store[f"idempotency:{key}"] = json.dumps(
        {"body": {"error": "Stale response"}, "status_code": invalid_status}
    )
    calls = _register_idempotent_route(app, body, 201)

    response = app.test_client().post(
        "/idempotent-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 201
    assert response.get_json() == body
    assert calls["count"] == 1
    cached = json.loads(app.fake_redis.store[f"idempotency:{key}"])
    _assert_cached_response_envelope(cached, body, 201)


def test_concurrent_same_key_returns_in_progress_then_replays_cached_response(app):
    started = threading.Event()
    release = threading.Event()
    calls = {"count": 0}

    def view():
        calls["count"] += 1
        started.set()
        assert release.wait(timeout=2)
        return jsonify({"message": "Created"}), 201

    app.add_url_rule(
        "/idempotent-blocking-test",
        endpoint="idempotent_blocking_test",
        view_func=idempotent(ttl=300)(view),
        methods=["POST"],
    )

    first_result = {}

    def first_request():
        client = app.test_client()
        first_result["response"] = client.post(
            "/idempotent-blocking-test",
            data=b'{"a":1}',
            headers={"Idempotency-Key": "concurrent-same-key"},
        )

    thread = threading.Thread(target=first_request)
    thread.start()
    assert started.wait(timeout=2)
    assert calls["count"] == 1

    second_response = app.test_client().post(
        "/idempotent-blocking-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": "concurrent-same-key"},
    )

    assert second_response.status_code == 409
    assert second_response.get_json() == {
        "error": "Idempotency request already in progress"
    }
    assert calls["count"] == 1
    assert "idempotency:concurrent-same-key" not in app.fake_redis.store

    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()

    first_response = first_result["response"]
    assert first_response.status_code == 201
    assert first_response.get_json() == {"message": "Created"}

    replay_response = app.test_client().post(
        "/idempotent-blocking-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": "concurrent-same-key"},
    )

    assert replay_response.status_code == 201
    assert replay_response.get_json() == {"message": "Created"}
    assert calls["count"] == 1


def test_different_keys_can_execute_in_parallel(app):
    first_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()
    calls = {"count": 0}

    def view():
        calls["count"] += 1
        if calls["count"] == 1:
            first_started.set()
        else:
            second_started.set()
        assert release.wait(timeout=2)
        return jsonify({"message": "Created"}), 201

    app.add_url_rule(
        "/idempotent-parallel-keys-test",
        endpoint="idempotent_parallel_keys_test",
        view_func=idempotent(ttl=300, lock_ttl=17)(view),
        methods=["POST"],
    )

    responses = {}

    def post_with_key(name, key):
        responses[name] = app.test_client().post(
            "/idempotent-parallel-keys-test",
            data=b'{"a":1}',
            headers={"Idempotency-Key": key},
        )

    first_thread = threading.Thread(target=post_with_key, args=("first", "parallel-a"))
    second_thread = threading.Thread(target=post_with_key, args=("second", "parallel-b"))
    first_thread.start()
    assert first_started.wait(timeout=2)
    second_thread.start()
    assert second_started.wait(timeout=2)

    assert calls["count"] == 2
    assert app.fake_redis.get("idempotency:parallel-a:lock") is not None
    assert app.fake_redis.get("idempotency:parallel-b:lock") is not None
    assert app.fake_redis.ttls["idempotency:parallel-a:lock"] == 17
    assert app.fake_redis.ttls["idempotency:parallel-b:lock"] == 17

    release.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert responses["first"].status_code == 201
    assert responses["second"].status_code == 201


def test_server_error_releases_lock_without_caching(app):
    calls = _register_idempotent_route(
        app,
        {"error": "Temporary failure"},
        503,
        rule="/idempotent-server-error-lock-test",
        endpoint="idempotent_server_error_lock_test",
    )
    key = "server-error-lock-release"

    response = app.test_client().post(
        "/idempotent-server-error-lock-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 503
    assert calls["count"] == 1
    assert f"idempotency:{key}" not in app.fake_redis.store
    assert app.fake_redis.exists(f"idempotency:{key}:lock") == 0


def test_exception_releases_lock_and_propagates(app):
    calls = {"count": 0}

    def view():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")
        return jsonify({"message": "Recovered"}), 200

    app.add_url_rule(
        "/idempotent-exception-test",
        endpoint="idempotent_exception_test",
        view_func=idempotent(ttl=300)(view),
        methods=["POST"],
    )
    key = "exception-lock-release"

    with pytest.raises(RuntimeError):
        app.test_client().post(
            "/idempotent-exception-test",
            data=b'{"a":1}',
            headers={"Idempotency-Key": key},
        )

    assert app.fake_redis.exists(f"idempotency:{key}:lock") == 0

    response = app.test_client().post(
        "/idempotent-exception-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Recovered"}
    assert calls["count"] == 2


def test_expired_lock_allows_new_execution(app):
    calls = _register_idempotent_route(
        app,
        {"message": "Created"},
        201,
        rule="/idempotent-expired-lock-test",
        endpoint="idempotent_expired_lock_test",
    )
    key = "expired-lock"
    app.fake_redis.set(f"idempotency:{key}:lock", "old-token", nx=True, ex=30)
    app.fake_redis.advance_time(31)

    response = app.test_client().post(
        "/idempotent-expired-lock-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 201
    assert calls["count"] == 1


def test_release_lock_does_not_delete_lock_owned_by_new_token(app):
    lock_key = "idempotency:ownership:lock"
    app.fake_redis.set(lock_key, "new-token", nx=True, ex=30)

    _release_lock_if_owner(lock_key, "old-token")

    assert app.fake_redis.get(lock_key) == "new-token"


def test_release_lock_deletes_lock_for_current_token(app):
    lock_key = "idempotency:owner-release:lock"
    app.fake_redis.set(lock_key, "owner-token", nx=True, ex=30)

    _release_lock_if_owner(lock_key, "owner-token")

    assert app.fake_redis.exists(lock_key) == 0


def test_cache_created_after_lock_acquisition_is_replayed_without_executing_view(
    app,
    monkeypatch,
):
    key = "cache-after-lock-acquire"
    redis_key = f"idempotency:{key}"
    cached_body = {"message": "Cached response"}
    cached = json.dumps({"body": cached_body, "status_code": 202})
    calls = _register_idempotent_route(
        app,
        {"message": "Should not execute"},
        201,
        rule="/idempotent-cache-after-lock-test",
        endpoint="idempotent_cache_after_lock_test",
    )

    def acquire_and_seed(lock_key, token, lock_ttl):
        acquired = app.fake_redis.set(lock_key, token, nx=True, ex=lock_ttl)
        app.fake_redis.setex(redis_key, 300, cached)
        return acquired

    monkeypatch.setattr("security.idempotency._acquire_lock", acquire_and_seed)

    response = app.test_client().post(
        "/idempotent-cache-after-lock-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 202
    assert response.get_json() == cached_body
    assert calls["count"] == 0


def test_cache_created_after_lock_contention_is_replayed_instead_of_in_progress(
    app,
    monkeypatch,
):
    key = "cache-after-lock-contention"
    redis_key = f"idempotency:{key}"
    cached_body = {"message": "Cached response"}
    cached = json.dumps({"body": cached_body, "status_code": 200})
    calls = _register_idempotent_route(
        app,
        {"message": "Should not execute"},
        201,
        rule="/idempotent-cache-after-contention-test",
        endpoint="idempotent_cache_after_contention_test",
    )

    def contend_and_seed(_lock_key, _token, _lock_ttl):
        app.fake_redis.setex(redis_key, 300, cached)
        return False

    monkeypatch.setattr("security.idempotency._acquire_lock", contend_and_seed)

    response = app.test_client().post(
        "/idempotent-cache-after-contention-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 200
    assert response.get_json() == cached_body
    assert calls["count"] == 0


def test_response_cache_key_and_ttl_remain_unchanged(app):
    calls = _register_idempotent_route(
        app,
        {"message": "Created"},
        201,
        rule="/idempotent-response-ttl-test",
        endpoint="idempotent_response_ttl_test",
    )
    key = "response-ttl"

    response = app.test_client().post(
        "/idempotent-response-ttl-test",
        data=b'{"a":1}',
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 201
    assert calls["count"] == 1
    assert f"idempotency:{key}" in app.fake_redis.store
    assert app.fake_redis.ttls[f"idempotency:{key}"] == 300
    assert app.fake_redis.exists(f"idempotency:{key}:lock") == 0
