import json

import pytest
from flask import Flask, jsonify

from security.idempotency import idempotent


def _register_idempotent_route(app, body, status_code):
    calls = {"count": 0}

    def view():
        calls["count"] += 1
        return jsonify(body), status_code

    app.add_url_rule(
        "/idempotent-test",
        endpoint="idempotent_test",
        view_func=idempotent(ttl=300)(view),
        methods=["POST"],
    )
    return calls


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

    first_response = client.post("/idempotent-test", headers=headers)
    replay_response = client.post("/idempotent-test", headers=headers)

    assert first_response.status_code == status_code
    assert first_response.get_json() == body
    assert replay_response.status_code == status_code
    assert replay_response.get_json() == body
    assert calls["count"] == 1

    cached = json.loads(app.fake_redis.store[f"idempotency:replay-{status_code}"])
    assert cached == {"body": body, "status_code": status_code}


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
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 201
    assert response.get_json() == body
    assert calls["count"] == 1
    assert json.loads(app.fake_redis.store[f"idempotency:{key}"]) == {
        "body": body,
        "status_code": 201,
    }


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
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 201
    assert response.get_json() == body
    assert calls["count"] == 1
    assert json.loads(app.fake_redis.store[f"idempotency:{key}"]) == {
        "body": body,
        "status_code": 201,
    }
