import hashlib
import hmac

from config import Config
from security.hmac import build_signed_message, sign_payload


def test_build_signed_message_uses_timestamp_separator_and_raw_body():
    assert build_signed_message("1700000000", b'{"a":1}') == b'1700000000.{"a":1}'


def test_sign_payload_uses_timestamp_bound_message(monkeypatch):
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-webhook-secret")
    raw_body = b'{"event_id":"evt_1"}'
    timestamp = "1700000000"

    expected_digest = hmac.new(
        b"test-webhook-secret",
        b'1700000000.{"event_id":"evt_1"}',
        hashlib.sha256,
    ).hexdigest()

    assert sign_payload(timestamp, raw_body) == f"sha256={expected_digest}"


def test_sign_payload_changes_when_timestamp_or_body_changes(monkeypatch):
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-webhook-secret")
    raw_body = b'{"event_id":"evt_1"}'

    signature = sign_payload("1700000000", raw_body)

    assert signature.startswith("sha256=")
    assert signature == signature.lower()
    assert signature != sign_payload("1700000001", raw_body)
    assert signature != sign_payload("1700000000", b'{"event_id":"evt_2"}')
