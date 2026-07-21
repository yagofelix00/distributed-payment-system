from decimal import Decimal

import hashlib
import hmac
import json
import time
import uuid

import pytest
from flask import Flask, jsonify, request

from db_models.charges import Charge, ChargeStatus
from repository.database import db
from routes.charges import charges_bp
from routes.webhooks import webhooks_bp

CHARGES_BASE = "/payment/charges"



def _sign_payload(secret, payload_bytes):
    digest = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


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
    monkeypatch.setattr("services.pix_webhook_service.redis_client", fake_redis)
    monkeypatch.setattr("security.idempotency.redis_client", fake_redis)
    monkeypatch.setattr("security.webhook_event_deduplication.redis_client", fake_redis)

    app.fake_redis = fake_redis

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def payment_client(app):
    return app.test_client()


@pytest.fixture
def bank_client(payment_client):
    bank_app = Flask(__name__)
    bank_app.config["TESTING"] = True
    bank_app.bank_charges = {}
    webhook_secret = "test-webhook-secret"

    @bank_app.post("/bank/pix/charges")
    def create_bank_charge():
        data = request.get_json(silent=True) or {}
        external_id = data.get("external_id")
        value = data.get("value")
        webhook_url = data.get("webhook_url")

        if not external_id or value is None or not webhook_url:
            return jsonify({"error": "Invalid payload"}), 400

        bank_app.bank_charges[external_id] = {
            "external_id": external_id,
            "value": value,
            "webhook_url": webhook_url,
            "status": "PENDING",
        }
        return jsonify({"message": "Charge registered in bank"}), 201

    @bank_app.post("/bank/pix/pay")
    def pay_bank_charge():
        data = request.get_json(silent=True) or {}
        external_id = data.get("external_id")
        event_id = data.get("event_id") or f"evt_{uuid.uuid4()}"

        if not external_id:
            return jsonify({"error": "Invalid payload"}), 400

        charge = bank_app.bank_charges.get(external_id)
        if not charge:
            return jsonify({"error": "Charge not found"}), 404

        payload = {
            "event_id": event_id,
            "external_id": external_id,
            "value": charge["value"],
            "status": "PAID",
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()

        webhook_response = payment_client.post(
            "/webhooks/pix",
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Timestamp": str(int(time.time())),
                "X-Signature": _sign_payload(webhook_secret, payload_bytes),
                "X-Event-Id": event_id,
                "Idempotency-Key": event_id,
            },
        )

        charge["status"] = "PAID"
        return jsonify(
            {
                "event_id": event_id,
                "webhook_status_code": webhook_response.status_code,
                "webhook_body": webhook_response.get_json(),
            }
        ), 200

    return bank_app.test_client()


def test_create_charge_without_value_returns_400(payment_client):
    response = payment_client.post(CHARGES_BASE, json={})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Value is required"}


def test_create_charge_with_zero_value_returns_400(payment_client):
    response = payment_client.post(CHARGES_BASE, json={"value": 0})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid value"}


def _create_charge_and_register_bank(payment_client, bank_client, value=100.0):
    create_response = payment_client.post(CHARGES_BASE, json={"value": value})
    assert create_response.status_code == 201
    charge_data = create_response.get_json()

    register_response = bank_client.post(
        "/bank/pix/charges",
        json={
            "external_id": charge_data["external_id"],
            "value": value,
            "webhook_url": "/webhooks/pix",
        },
    )
    assert register_response.status_code == 201

    return charge_data


def test_pix_e2e_happy_path_create_pay_webhook_paid(payment_client, bank_client):
    charge_data = _create_charge_and_register_bank(payment_client, bank_client, value=120.0)

    pay_response = bank_client.post(
        "/bank/pix/pay",
        json={"external_id": charge_data["external_id"]},
    )
    assert pay_response.status_code == 200
    assert pay_response.get_json()["webhook_status_code"] == 200

    status_response = payment_client.get(f"{CHARGES_BASE}/{charge_data['id']}")
    assert status_response.status_code == 200
    assert status_response.get_json()["status"] == ChargeStatus.PAID.value


def test_pix_e2e_webhook_after_ttl_expired_results_in_expired(payment_client, bank_client, app):
    charge_data = _create_charge_and_register_bank(payment_client, bank_client, value=95.5)
    ttl_key = f"charge:ttl:{charge_data['external_id']}"

    assert app.fake_redis.exists(ttl_key) == 1
    app.fake_redis.delete(ttl_key)

    pay_response = bank_client.post(
        "/bank/pix/pay",
        json={"external_id": charge_data["external_id"]},
    )
    assert pay_response.status_code == 200
    assert pay_response.get_json()["webhook_status_code"] == 200
    assert pay_response.get_json()["webhook_body"]["message"] == "Expired charge ignored"

    status_response = payment_client.get(f"{CHARGES_BASE}/{charge_data['id']}")
    assert status_response.status_code == 200
    assert status_response.get_json()["status"] == ChargeStatus.EXPIRED.value


def test_get_charge_expires_pending_charge_when_read_cache_is_stale(payment_client, bank_client, app):
    charge_data = _create_charge_and_register_bank(payment_client, bank_client, value=42.0)
    ttl_key = f"charge:ttl:{charge_data['external_id']}"
    cache_key = f"charge:{charge_data['id']}"

    assert app.fake_redis.exists(ttl_key) == 1

    first_response = payment_client.get(f"{CHARGES_BASE}/{charge_data['id']}")
    assert first_response.status_code == 200
    assert first_response.get_json()["status"] == ChargeStatus.PENDING.value
    assert app.fake_redis.exists(cache_key) == 1

    app.fake_redis.delete(ttl_key)
    assert app.fake_redis.exists(ttl_key) == 0
    assert app.fake_redis.exists(cache_key) == 1

    second_response = payment_client.get(f"{CHARGES_BASE}/{charge_data['id']}")
    assert second_response.status_code == 200
    assert second_response.get_json()["status"] == ChargeStatus.EXPIRED.value

    with app.app_context():
        refreshed = db.session.get(Charge, charge_data["id"])
        assert refreshed.status == ChargeStatus.EXPIRED.value


def test_pix_e2e_duplicate_webhook_event_is_ignored_and_final_status_paid(payment_client, bank_client, app):
    charge_data = _create_charge_and_register_bank(payment_client, bank_client, value=77.0)
    duplicated_event_id = "evt_duplicate_001"

    first_pay_response = bank_client.post(
        "/bank/pix/pay",
        json={
            "external_id": charge_data["external_id"],
            "event_id": duplicated_event_id,
        },
    )
    assert first_pay_response.status_code == 200
    assert first_pay_response.get_json()["webhook_status_code"] == 200

    with app.app_context():
        first_paid_at = Charge.query.get(charge_data["id"]).paid_at
        assert first_paid_at is not None

    second_pay_response = bank_client.post(
        "/bank/pix/pay",
        json={
            "external_id": charge_data["external_id"],
            "event_id": duplicated_event_id,
        },
    )
    assert second_pay_response.status_code == 200
    assert second_pay_response.get_json()["webhook_status_code"] == 200

    with app.app_context():
        refreshed = Charge.query.get(charge_data["id"])
        assert refreshed.status == ChargeStatus.PAID.value
        assert refreshed.paid_at == first_paid_at

    status_response = payment_client.get(f"{CHARGES_BASE}/{charge_data['id']}")
    assert status_response.status_code == 200
    assert status_response.get_json()["status"] == ChargeStatus.PAID.value


def test_create_charge_with_decimal_value_persists_decimal_and_returns_json_number(
    payment_client, bank_client, app
):
    response = payment_client.post(CHARGES_BASE, json={"value": 0.10})

    assert response.status_code == 201
    charge_data = response.get_json()

    with app.app_context():
        refreshed = db.session.get(Charge, charge_data["id"])
        assert refreshed.value == Decimal("0.10")
        assert isinstance(refreshed.value, Decimal)
        assert app.fake_redis.exists(f"charge:ttl:{refreshed.external_id}") == 1

    register_response = bank_client.post(
        "/bank/pix/charges",
        json={
            "external_id": charge_data["external_id"],
            "value": 0.10,
            "webhook_url": "/webhooks/pix",
        },
    )
    assert register_response.status_code == 201

    pay_response = bank_client.post(
        "/bank/pix/pay",
        json={"external_id": charge_data["external_id"]},
    )
    assert pay_response.status_code == 200
    assert pay_response.get_json()["webhook_status_code"] == 200

    status_response = payment_client.get(f"{CHARGES_BASE}/{charge_data['id']}")

    assert status_response.status_code == 200
    status_body = status_response.get_json()
    assert status_body["value"] == 0.1
    assert isinstance(status_body["value"], float)
    assert status_body["status"] == ChargeStatus.PAID.value
    cache_key = f"charge:{charge_data['id']}"
    assert app.fake_redis.exists(cache_key) == 1

    with app.app_context():
        refreshed = db.session.get(Charge, charge_data["id"])
        refreshed.value = Decimal("9.99")
        db.session.commit()

    cached_response = payment_client.get(f"{CHARGES_BASE}/{charge_data['id']}")

    assert cached_response.status_code == 200
    assert cached_response.get_json() == status_body
    assert isinstance(cached_response.get_json()["value"], float)


def test_create_charge_rejects_more_than_two_decimal_places_without_ttl(payment_client, app):
    response = payment_client.post(CHARGES_BASE, json={"value": 10.001})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid value"}

    with app.app_context():
        assert Charge.query.count() == 0
    assert app.fake_redis.store == {}


@pytest.mark.parametrize("invalid_value", [True, None, "abc", "NaN", "Infinity"])
def test_create_charge_rejects_invalid_money_values(payment_client, invalid_value):
    response = payment_client.post(CHARGES_BASE, json={"value": invalid_value})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid value"}


def test_pix_e2e_decimal_sensitive_value_paid(payment_client, bank_client):
    charge_data = _create_charge_and_register_bank(payment_client, bank_client, value=19.99)

    pay_response = bank_client.post(
        "/bank/pix/pay",
        json={"external_id": charge_data["external_id"]},
    )

    assert pay_response.status_code == 200
    assert pay_response.get_json()["webhook_status_code"] == 200

    status_response = payment_client.get(f"{CHARGES_BASE}/{charge_data['id']}")

    assert status_response.status_code == 200
    assert status_response.get_json()["status"] == ChargeStatus.PAID.value
    assert status_response.get_json()["value"] == 19.99
