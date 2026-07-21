from flask import Flask

from routes import dlq as dlq_routes


def test_dlq_replay_redispatches_payload_and_marks_replayed_after_success(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(dlq_routes.dlq_bp)

    record = {
        "event_id": "evt_dlq_replay",
        "url": "http://receiver/webhooks/pix",
        "payload": {
            "event_id": "evt_dlq_replay",
            "external_id": "ext-dlq-replay",
            "value": 100.0,
            "status": "PAID",
        },
        "headers": {
            "X-Timestamp": "1",
            "X-Event-Id": "evt_dlq_replay",
        },
    }
    calls = []
    marked = []

    monkeypatch.setattr(dlq_routes, "get_by_event_id", lambda event_id: record)

    def fake_send_webhook(url, payload):
        calls.append({"url": url, "payload": payload})
        return True

    monkeypatch.setattr(dlq_routes, "send_webhook", fake_send_webhook)
    monkeypatch.setattr(dlq_routes, "mark_replayed", lambda event_id: marked.append(event_id))

    response = app.test_client().post("/bank/dlq/replay", json={"event_id": "evt_dlq_replay"})

    assert response.status_code == 200
    assert response.get_json() == {"message": "replayed", "event_id": "evt_dlq_replay"}
    assert calls == [{"url": record["url"], "payload": record["payload"]}]
    assert marked == ["evt_dlq_replay"]


def test_dlq_replay_failure_does_not_mark_replayed_or_reuse_signature(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(dlq_routes.dlq_bp)

    record = {
        "event_id": "evt_dlq_replay_failure",
        "url": "http://receiver/webhooks/pix",
        "payload": {
            "event_id": "evt_dlq_replay_failure",
            "external_id": "ext-dlq-replay-failure",
            "value": 100.0,
            "status": "PAID",
        },
        "headers": {
            "X-Timestamp": "1",
            "X-Event-Id": "evt_dlq_replay_failure",
            "X-Signature": "sha256=oldsignature",
        },
    }
    calls = []
    marked = []

    monkeypatch.setattr(dlq_routes, "get_by_event_id", lambda event_id: record)

    def fake_send_webhook(url, payload):
        calls.append({"url": url, "payload": payload})
        return False

    monkeypatch.setattr(dlq_routes, "send_webhook", fake_send_webhook)
    monkeypatch.setattr(dlq_routes, "mark_replayed", lambda event_id: marked.append(event_id))

    response = app.test_client().post(
        "/bank/dlq/replay",
        json={"event_id": "evt_dlq_replay_failure"},
    )

    assert response.status_code == 502
    assert response.get_json() == {
        "message": "replay_failed",
        "event_id": "evt_dlq_replay_failure",
    }
    assert calls == [{"url": record["url"], "payload": record["payload"]}]
    assert marked == []
