import hashlib
import hmac
import json

from config import Config
from services import webhook_dispatcher


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def _expected_signature(secret, timestamp, body):
    digest = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def test_send_webhook_recalculates_timestamp_and_signature_per_retry(monkeypatch):
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(webhook_dispatcher, "get_request_id", lambda: "req-test")
    monkeypatch.setattr(webhook_dispatcher.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(webhook_dispatcher.random, "uniform", lambda start, end: 0)

    timestamps = iter([1700000000, 1700000001])
    monkeypatch.setattr(webhook_dispatcher.time, "time", lambda: next(timestamps))

    calls = []

    def fake_post(url, data, headers, timeout):
        calls.append({
            "url": url,
            "data": data,
            "headers": dict(headers),
            "timeout": timeout,
        })
        return FakeResponse(500 if len(calls) == 1 else 200, "temporary failure")

    monkeypatch.setattr(webhook_dispatcher.requests, "post", fake_post)

    payload = {
        "event_id": "evt_retry_signature",
        "external_id": "ext-retry-signature-ação",
        "value": 100.0,
        "status": "PAID",
    }

    delivered = webhook_dispatcher.send_webhook(
        "http://receiver/webhooks/pix",
        payload,
        max_retries=2,
    )

    expected_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    expected_body_bytes = expected_body.encode("utf-8")
    assert delivered is True
    assert [call["data"] for call in calls] == [
        expected_body_bytes,
        expected_body_bytes,
    ]
    assert [call["headers"]["X-Timestamp"] for call in calls] == ["1700000000", "1700000001"]
    assert calls[0]["headers"]["X-Signature"] == _expected_signature(
        "test-webhook-secret",
        "1700000000",
        expected_body,
    )
    assert calls[1]["headers"]["X-Signature"] == _expected_signature(
        "test-webhook-secret",
        "1700000001",
        expected_body,
    )
    assert calls[0]["headers"]["X-Signature"] != calls[1]["headers"]["X-Signature"]
    assert calls[0]["headers"]["X-Event-Id"] == "evt_retry_signature"
    assert calls[1]["headers"]["X-Event-Id"] == "evt_retry_signature"
    assert calls[0]["headers"]["X-Request-Id"] == "req-test"


def test_failed_webhook_dlq_persists_last_headers_without_signature(monkeypatch):
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(webhook_dispatcher, "get_request_id", lambda: "req-test")
    monkeypatch.setattr(webhook_dispatcher.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(webhook_dispatcher.time, "time", lambda: 1700000000)

    def fake_post(url, data, headers, timeout):
        return FakeResponse(500, "temporary failure")

    enqueued = {}

    def fake_enqueue_failed_webhook(**kwargs):
        enqueued.update(kwargs)

    monkeypatch.setattr(webhook_dispatcher.requests, "post", fake_post)
    monkeypatch.setattr(webhook_dispatcher, "enqueue_failed_webhook", fake_enqueue_failed_webhook)

    payload = {
        "event_id": "evt_dlq_headers",
        "external_id": "ext-dlq-headers",
        "value": 100.0,
        "status": "PAID",
    }

    delivered = webhook_dispatcher.send_webhook(
        "http://receiver/webhooks/pix",
        payload,
        max_retries=1,
    )

    assert delivered is False
    assert enqueued["payload"] == payload
    assert enqueued["headers"]["X-Timestamp"] == "1700000000"
    assert enqueued["headers"]["X-Event-Id"] == "evt_dlq_headers"
    assert "X-Signature" not in enqueued["headers"]
