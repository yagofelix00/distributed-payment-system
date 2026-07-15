import hmac
import hashlib
import json
import time

import pytest
from flask import Flask

from repository.database import db
from db_models.charges import Charge, ChargeStatus
from routes.charges import charges_bp
from routes.webhooks import webhooks_bp
from services.charge_state_machine import (
    ChargeState,
    InvalidChargeTransition,
    transition_charge,
)



@pytest.fixture
def app(monkeypatch, fake_redis):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["WEBHOOK_SECRET"] = "test-webhook-secret"

    db.init_app(app)
    app.register_blueprint(charges_bp)
    app.register_blueprint(webhooks_bp)

    monkeypatch.setattr("routes.charges.redis_client", fake_redis)
    monkeypatch.setattr("services.charge_service.redis_client", fake_redis)
    monkeypatch.setattr("routes.webhooks.redis_client", fake_redis)
    monkeypatch.setattr("security.idempotency.redis_client", fake_redis)

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _create_charge(value=100.0, status=ChargeStatus.PENDING, external_id="ext-1"):
    status_value = status.value if hasattr(status, "value") else str(status)
    charge = Charge(value=value, status=status_value, external_id=external_id)
    db.session.add(charge)
    db.session.commit()
    return charge


def _sign_payload(secret, payload_bytes):
    digest = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_transition_pending_to_paid(app):
    with app.app_context():
        charge = _create_charge(status=ChargeStatus.PENDING, external_id="ext-p2paid")
        transition_charge(charge, ChargeState.PAID)

        refreshed = Charge.query.get(charge.id)
        assert refreshed.status == ChargeStatus.PAID.value
        assert refreshed.paid_at is not None


def test_transition_pending_to_expired(app):
    with app.app_context():
        charge = _create_charge(status=ChargeStatus.PENDING, external_id="ext-p2exp")
        transition_charge(charge, ChargeState.EXPIRED)

        refreshed = Charge.query.get(charge.id)
        assert refreshed.status == ChargeStatus.EXPIRED.value


def test_invalid_transition_paid_to_expired(app):
    with app.app_context():
        charge = _create_charge(status=ChargeStatus.PAID, external_id="ext-paid2exp")

        with pytest.raises(InvalidChargeTransition):
            transition_charge(charge, ChargeState.EXPIRED)


def test_invalid_transition_expired_to_paid(app):
    with app.app_context():
        charge = _create_charge(status=ChargeStatus.EXPIRED, external_id="ext-exp2paid")

        with pytest.raises(InvalidChargeTransition):
            transition_charge(charge, ChargeState.PAID)


def test_webhook_paid_ignored_for_expired(client, app):
    with app.app_context():
        charge = _create_charge(
            value=55.0,
            status=ChargeStatus.EXPIRED,
            external_id="ext-webhook-expired",
        )
        charge_id = charge.id

    payload = {
        "event_id": "evt_test_001",
        "external_id": "ext-webhook-expired",
        "value": 55.0,
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
            "Idempotency-Key": "idem-expired-webhook",
            "X-Event-Id": "evt_test_001",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["message"] == "Charge already processed"

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.EXPIRED
