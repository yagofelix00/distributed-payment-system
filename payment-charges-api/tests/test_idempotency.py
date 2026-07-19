import json

import pytest
from flask import Flask, jsonify

from security.idempotency import idempotent


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
