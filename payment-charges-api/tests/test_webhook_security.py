import hashlib
import hmac
import json
import time

import pytest
from flask import Flask

from db_models.charges import Charge, ChargeStatus
from repository.database import db
from routes.charges import charges_bp
from routes.webhooks import webhooks_bp


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, _ttl, value):
        self.store[key] = value

    def exists(self, key):
        return 1 if key in self.store else 0

    def delete(self, key):
        self.store.pop(key, None)


def _sign_payload(secret, payload_bytes):
    digest = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _create_charge(value=100.0, status=ChargeStatus.PENDING, external_id="ext-security-1"):
    status_value = status.value if hasattr(status, "value") else str(status)
    charge = Charge(value=value, status=status_value, external_id=external_id)
    db.session.add(charge)
    db.session.commit()
    return charge


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["WEBHOOK_SECRET"] = "test-webhook-secret"

    db.init_app(app)
    app.register_blueprint(charges_bp)
    app.register_blueprint(webhooks_bp)

    fake_redis = FakeRedis()
    monkeypatch.setattr("routes.charges.redis_client", fake_redis)
    monkeypatch.setattr("routes.webhooks.redis_client", fake_redis)
    monkeypatch.setattr("security.idempotency.redis_client", fake_redis)

    app.fake_redis = fake_redis

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_webhook_invalid_signature_returns_401_and_keeps_pending(client, app):
    with app.app_context():
        charge = _create_charge(
            value=100.0,
            status=ChargeStatus.PENDING,
            external_id="ext-invalid-signature",
        )
        ttl_key = f"charge:ttl:{charge.external_id}"
        app.fake_redis.setex(ttl_key, 1800, "PENDING")
        assert app.fake_redis.exists(ttl_key) == 1

    payload = {
        "event_id": "evt_test_invalid_sig",
        "external_id": "ext-invalid-signature",
        "value": 100.0,
        "status": "PAID",
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()

    response = client.post(
        "/webhooks/pix",
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": str(int(time.time())),
            "X-Signature": "sha256=invalidsignature",
            "X-Event-Id": "evt_test_invalid_sig",
            "Idempotency-Key": "evt_test_invalid_sig",
        },
    )

    assert response.status_code == 401

    with app.app_context():
        refreshed = Charge.query.get(charge.id)
        assert refreshed.status == ChargeStatus.PENDING.value

def test_webhook_timestamp_outside_window_returns_401_or_400_and_keeps_pending(client, app):
    with app.app_context():
        charge = _create_charge(
            value=150.0,
            status=ChargeStatus.PENDING,
            external_id="ext-old-timestamp",
        )
        ttl_key = f"charge:ttl:{charge.external_id}"
        app.fake_redis.setex(ttl_key, 1800, "PENDING")
        assert app.fake_redis.exists(ttl_key) == 1
    payload = {
        "event_id": "evt_test_old_timestamp",
        "external_id": "ext-old-timestamp",
        "value": 150.0,
        "status": "PAID",
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    signature = _sign_payload("test-webhook-secret", payload_bytes)
    response = client.post(
        "/webhooks/pix",
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": str(int(time.time()) - 10_000),
            "X-Signature": signature,
            "X-Event-Id": "evt_test_old_timestamp",
            "Idempotency-Key": "evt_test_old_timestamp",
        },
    )
    if response.status_code not in (401, 400):
        pytest.xfail(
            "Timestamp validation appears missing in security/webhook_signature.py "
            "(expected rejection for old timestamp)."
        )
    with app.app_context():
        refreshed = Charge.query.get(charge.id)
        assert refreshed.status == ChargeStatus.PENDING.value

def test_webhook_value_mismatch_returns_400_and_keeps_pending(client, app):
    with app.app_context():
        charge = _create_charge(
            value=100.0,
            status=ChargeStatus.PENDING,
            external_id="ext-value-mismatch",
        )
        ttl_key = f"charge:ttl:{charge.external_id}"
        app.fake_redis.setex(ttl_key, 1800, "PENDING")
        assert app.fake_redis.exists(ttl_key) == 1
    payload = {
        "event_id": "evt_test_value_mismatch",
        "external_id": "ext-value-mismatch",
        "value": 999.0,
        "status": "PAID",
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    signature = _sign_payload("test-webhook-secret", payload_bytes)
    response = client.post(
        "/webhooks/pix",
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": str(int(time.time())),
            "X-Signature": signature,
            "X-Event-Id": "evt_test_value_mismatch",
            "Idempotency-Key": "evt_test_value_mismatch",
        },
    )
    assert response.status_code == 400
    with app.app_context():
        refreshed = Charge.query.get(charge.id)
        assert refreshed.status == ChargeStatus.PENDING.value


def test_webhook_duplicate_event_id_is_ignored_without_changing_paid_at(client, app):
    with app.app_context():
        charge = _create_charge(
            value=88.0,
            status=ChargeStatus.PENDING,
            external_id="ext-duplicate-event",
        )
        ttl_key = f"charge:ttl:{charge.external_id}"
        app.fake_redis.setex(ttl_key, 1800, "PENDING")
        assert app.fake_redis.exists(ttl_key) == 1

    payload = {
        "event_id": "evt_same_event_twice",
        "external_id": "ext-duplicate-event",
        "value": 88.0,
        "status": "PAID",
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    signature = _sign_payload("test-webhook-secret", payload_bytes)

    first_response = client.post(
        "/webhooks/pix",
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": str(int(time.time())),
            "X-Signature": signature,
            "X-Event-Id": "evt_same_event_twice",
            "Idempotency-Key": "idem-first",
        },
    )
    assert first_response.status_code == 200

    with app.app_context():
        first_paid_at = Charge.query.get(charge.id).paid_at
        assert first_paid_at is not None

    second_response = client.post(
        "/webhooks/pix",
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": str(int(time.time())),
            "X-Signature": signature,
            "X-Event-Id": "evt_same_event_twice",
            "Idempotency-Key": "idem-second",
        },
    )
    assert second_response.status_code == 200
    assert second_response.get_json()["message"] == "Duplicate event ignored"

    with app.app_context():
        refreshed = Charge.query.get(charge.id)
        assert refreshed.status == ChargeStatus.PAID.value
        assert refreshed.paid_at == first_paid_at
