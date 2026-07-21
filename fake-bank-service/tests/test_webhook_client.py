import hashlib
import hmac
import json

from config import Config
from clients import webhook_client


class FakeResponse:
    def raise_for_status(self):
        return None


def test_legacy_webhook_client_signs_and_sends_same_utf8_bytes(monkeypatch):
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(webhook_client.time, "time", lambda: 1700000000)

    calls = []

    def fake_post(url, data, headers, timeout):
        calls.append(
            {
                "url": url,
                "data": data,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setattr(webhook_client.requests, "post", fake_post)

    payload = {
        "event_id": "evt_cliente_utf8",
        "external_id": "ext_cliente_utf8",
        "description": "ação João 🚀",
        "value": 100.0,
        "status": "PAID",
    }

    webhook_client.send_webhook("http://receiver/webhooks/pix", payload)

    expected_data = json.dumps(payload).encode("utf-8")
    expected_digest = hmac.new(
        b"test-webhook-secret",
        b"1700000000." + expected_data,
        hashlib.sha256,
    ).hexdigest()

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "http://receiver/webhooks/pix"
    assert call["data"] == expected_data
    assert isinstance(call["data"], bytes)
    assert call["headers"]["X-Timestamp"] == "1700000000"
    assert call["headers"]["X-Signature"] == f"sha256={expected_digest}"
    assert call["headers"]["X-Event-Id"] == "evt_cliente_utf8"
    assert call["timeout"] == 5
