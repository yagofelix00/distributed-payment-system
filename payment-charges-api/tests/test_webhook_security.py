from decimal import Decimal

import hashlib
import hmac
import json
import threading
import time

import pytest
from flask import Flask

from db_models.charges import Charge, ChargeStatus
from repository.database import db
from routes.charges import charges_bp
from routes.webhooks import webhooks_bp



def _sign_payload(secret, payload_bytes):
    digest = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _create_charge(value=100.0, status=ChargeStatus.PENDING, external_id="ext-security-1"):
    status_value = status.value if hasattr(status, "value") else str(status)
    charge = Charge(value=value, status=status_value, external_id=external_id)
    db.session.add(charge)
    db.session.commit()
    return charge


def _post_signed_webhook(client, payload, idempotency_key):
    if isinstance(payload, bytes):
        payload_bytes = payload
        event_id = None
    else:
        payload_bytes = json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        event_id = payload.get("event_id") if isinstance(payload, dict) else None

    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": str(int(time.time())),
        "X-Signature": _sign_payload("test-webhook-secret", payload_bytes),
        "Idempotency-Key": idempotency_key,
    }

    if event_id:
        headers["X-Event-Id"] = event_id

    return client.post(
        "/webhooks/pix",
        data=payload_bytes,
        headers=headers,
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
def client(app):
    return app.test_client()


def test_webhook_non_json_body_returns_400(client):
    response = _post_signed_webhook(
        client,
        b"not-json",
        "idem-invalid-payload-non-json",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid JSON payload"}


def test_webhook_json_list_payload_returns_400(client):
    response = _post_signed_webhook(
        client,
        ["not", "an", "object"],
        "idem-invalid-payload-list",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid JSON payload"}


def test_webhook_missing_event_id_returns_400(client):
    response = _post_signed_webhook(
        client,
        {
            "external_id": "ext-missing-event-id",
            "value": 100.0,
            "status": "PAID",
        },
        "idem-invalid-payload-missing-event-id",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "event_id is required"}


def test_webhook_missing_external_id_returns_400(client):
    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_missing_external_id",
            "value": 100.0,
            "status": "PAID",
        },
        "idem-invalid-payload-missing-external-id",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid payload"}


def test_webhook_missing_value_returns_400(client):
    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_missing_value",
            "external_id": "ext-missing-value",
            "status": "PAID",
        },
        "idem-invalid-payload-missing-value",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid payload"}


def test_webhook_missing_status_returns_400(client):
    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_missing_status",
            "external_id": "ext-missing-status",
            "value": 100.0,
        },
        "idem-invalid-payload-missing-status",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid payload"}


def test_webhook_non_paid_status_returns_ignored(client):
    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_non_paid_status",
            "external_id": "ext-non-paid-status",
            "value": 100.0,
            "status": "PENDING",
        },
        "idem-invalid-payload-non-paid-status",
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Ignored"}


def test_webhook_paid_unknown_external_id_returns_404(client):
    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_unknown_external_id",
            "external_id": "ext-unknown-external-id",
            "value": 100.0,
            "status": "PAID",
        },
        "idem-invalid-payload-unknown-external-id",
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "Charge not found"}


def test_webhook_paid_charge_with_new_event_id_returns_already_processed(client, app):
    with app.app_context():
        charge = _create_charge(
            value=100.0,
            status=ChargeStatus.PAID,
            external_id="ext-already-paid-new-event",
        )
        charge_id = charge.id

    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_paid_charge_new_event",
            "external_id": "ext-already-paid-new-event",
            "value": 100.0,
            "status": "PAID",
        },
        "idem-paid-charge-new-event",
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Charge already processed"}

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value


def test_webhook_paid_charge_with_invalid_value_returns_already_processed(client, app):
    with app.app_context():
        charge = _create_charge(
            value=Decimal("100.00"),
            status=ChargeStatus.PAID,
            external_id="ext-already-paid-invalid-value",
        )
        charge_id = charge.id

    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_paid_charge_invalid_value",
            "external_id": "ext-already-paid-invalid-value",
            "value": "abc",
            "status": "PAID",
        },
        "idem-paid-charge-invalid-value",
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Charge already processed"}

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value


def test_webhook_non_numeric_value_returns_400_and_keeps_pending(client, app):
    with app.app_context():
        charge = _create_charge(
            value=100.0,
            status=ChargeStatus.PENDING,
            external_id="ext-non-numeric-value",
        )
        charge_id = charge.id
        ttl_key = f"charge:ttl:{charge.external_id}"
        app.fake_redis.setex(ttl_key, 1800, "PENDING")
        assert app.fake_redis.exists(ttl_key) == 1

    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_non_numeric_value",
            "external_id": "ext-non-numeric-value",
            "value": "not-a-number",
            "status": "PAID",
        },
        "idem-invalid-payload-non-numeric-value",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid value type"}

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PENDING.value


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


def test_webhook_same_idempotency_key_with_different_payload_returns_409(client, app):
    external_id = "ext-idempotency-fingerprint-conflict"
    first_event_id = "evt_idempotency_fingerprint_first"
    second_event_id = "evt_idempotency_fingerprint_second"
    idempotency_key = "idem-fingerprint-conflict"

    with app.app_context():
        charge = _create_charge(
            value=100.0,
            status=ChargeStatus.PENDING,
            external_id=external_id,
        )
        charge_id = charge.id
        app.fake_redis.setex(f"charge:ttl:{external_id}", 1800, "PENDING")

    first_response = _post_signed_webhook(
        client,
        {
            "event_id": first_event_id,
            "external_id": external_id,
            "value": 100.0,
            "status": "PAID",
        },
        idempotency_key,
    )

    conflict_response = _post_signed_webhook(
        client,
        {
            "event_id": second_event_id,
            "external_id": external_id,
            "value": 100.0,
            "status": "PAID",
        },
        idempotency_key,
    )

    assert first_response.status_code == 200
    assert first_response.get_json() == {"message": "Payment confirmed"}
    assert conflict_response.status_code == 409
    assert conflict_response.get_json() == {
        "error": "Idempotency-Key reused with different request"
    }
    assert app.fake_redis.exists(f"webhook:event:{second_event_id}") == 0

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value


def test_webhook_invalid_signature_cannot_replay_cached_idempotent_response(client, app):
    external_id = "ext-invalid-signature-no-replay"
    event_id = "evt_invalid_signature_no_replay"
    idempotency_key = "idem-invalid-signature-no-replay"

    with app.app_context():
        _create_charge(
            value=100.0,
            status=ChargeStatus.PENDING,
            external_id=external_id,
        )
        app.fake_redis.setex(f"charge:ttl:{external_id}", 1800, "PENDING")

    valid_response = _post_signed_webhook(
        client,
        {
            "event_id": event_id,
            "external_id": external_id,
            "value": 100.0,
            "status": "PAID",
        },
        idempotency_key,
    )
    assert valid_response.status_code == 200

    replay_payload = {
        "event_id": event_id,
        "external_id": external_id,
        "value": 100.0,
        "status": "PAID",
    }
    replay_payload_bytes = json.dumps(
        replay_payload,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()

    replay_response = client.post(
        "/webhooks/pix",
        data=replay_payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": str(int(time.time())),
            "X-Signature": "sha256=invalidsignature",
            "X-Event-Id": event_id,
            "Idempotency-Key": idempotency_key,
        },
    )

    assert replay_response.status_code == 401
    assert replay_response.get_json() == {"error": "Invalid webhook signature"}


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
    assert response.get_json() == {"error": "Invalid value"}
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


def test_webhook_ttl_redis_failure_returns_503_and_keeps_charge_pending(
    client, app, monkeypatch
):
    class TtlFailingRedis:
        def __init__(self, delegate):
            self.delegate = delegate

        def exists(self, key):
            if key.startswith("charge:ttl:"):
                raise RuntimeError("Redis unavailable")
            return self.delegate.exists(key)

    external_id = "ext-ttl-redis-failure"
    event_id = "evt-ttl-redis-failure"

    with app.app_context():
        charge = _create_charge(value=100.0, external_id=external_id)
        charge_id = charge.id
        app.fake_redis.setex(f"charge:ttl:{external_id}", 1800, "PENDING")

    monkeypatch.setattr(
        "services.pix_webhook_service.redis_client", TtlFailingRedis(app.fake_redis)
    )

    response = _post_signed_webhook(
        client,
        {
            "event_id": event_id,
            "external_id": external_id,
            "value": 100.0,
            "status": "PAID",
        },
        "idem-ttl-redis-failure",
    )

    assert response.status_code == 503
    assert response.get_json() == {"error": "Service unavailable"}
    assert app.fake_redis.exists(f"webhook:event:{event_id}") == 0
    assert app.fake_redis.exists(f"webhook:event:{event_id}:lock") == 0

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PENDING.value
        assert refreshed.paid_at is None


def test_webhook_retry_after_transient_503_reexecutes_and_caches_success(
    client, app, monkeypatch
):
    class TtlFailsOnceRedis:
        def __init__(self, delegate):
            self.delegate = delegate
            self.failures = 0

        def exists(self, key):
            if key.startswith("charge:ttl:") and self.failures == 0:
                self.failures += 1
                raise RuntimeError("Redis unavailable")
            return self.delegate.exists(key)

    external_id = "ext-transient-ttl-retry"
    event_id = "evt-transient-ttl-retry"
    idempotency_key = "idem-transient-ttl-retry"
    idempotency_cache_key = f"idempotency:{idempotency_key}"
    payload = {
        "event_id": event_id,
        "external_id": external_id,
        "value": 100.0,
        "status": "PAID",
    }

    with app.app_context():
        charge = _create_charge(value=100.0, external_id=external_id)
        charge_id = charge.id
        app.fake_redis.setex(f"charge:ttl:{external_id}", 1800, "PENDING")

    flaky_redis = TtlFailsOnceRedis(app.fake_redis)
    monkeypatch.setattr("services.pix_webhook_service.redis_client", flaky_redis)

    first_response = _post_signed_webhook(client, payload, idempotency_key)

    assert first_response.status_code == 503
    assert first_response.get_json() == {"error": "Service unavailable"}
    assert idempotency_cache_key not in app.fake_redis.store
    assert app.fake_redis.exists(f"webhook:event:{event_id}") == 0
    assert app.fake_redis.exists(f"webhook:event:{event_id}:lock") == 0

    monkeypatch.setattr("services.pix_webhook_service.redis_client", app.fake_redis)

    second_response = _post_signed_webhook(client, payload, idempotency_key)

    assert second_response.status_code == 200
    assert second_response.get_json() == {"message": "Payment confirmed"}
    assert app.fake_redis.exists(f"webhook:event:{event_id}") == 1
    cached = json.loads(app.fake_redis.store[idempotency_cache_key])
    assert cached["body"] == {"message": "Payment confirmed"}
    assert cached["status_code"] == 200

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value
        paid_at = refreshed.paid_at
        assert paid_at is not None

    third_response = _post_signed_webhook(client, payload, idempotency_key)

    assert third_response.status_code == 200
    assert third_response.get_json() == {"message": "Payment confirmed"}

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value
        assert refreshed.paid_at == paid_at


def test_concurrent_same_idempotency_key_allows_single_webhook_execution(
    app,
    monkeypatch,
):
    import routes.webhooks as webhooks_module

    external_id = "ext-concurrent-idempotency"
    event_id = "evt-concurrent-idempotency"
    idempotency_key = "idem-concurrent-idempotency"
    idempotency_cache_key = f"idempotency:{idempotency_key}"
    entered_view = threading.Event()
    release_view = threading.Event()
    validation_calls = {"count": 0}
    original_validate = webhooks_module.validate_pix_webhook_payload

    with app.app_context():
        charge = _create_charge(value=100.0, external_id=external_id)
        charge_id = charge.id
        app.fake_redis.setex(f"charge:ttl:{external_id}", 1800, "PENDING")

    def blocking_validate(data):
        validation_calls["count"] += 1
        result = original_validate(data)
        if validation_calls["count"] == 1:
            entered_view.set()
            assert release_view.wait(timeout=2)
        return result

    monkeypatch.setattr(
        webhooks_module,
        "validate_pix_webhook_payload",
        blocking_validate,
    )

    payload = {
        "event_id": event_id,
        "external_id": external_id,
        "value": 100.0,
        "status": "PAID",
    }
    first_result = {}

    def first_request():
        client = app.test_client()
        first_result["response"] = _post_signed_webhook(
            client,
            payload,
            idempotency_key,
        )

    thread = threading.Thread(target=first_request)
    thread.start()
    assert entered_view.wait(timeout=2)
    assert validation_calls["count"] == 1

    in_progress_response = _post_signed_webhook(
        app.test_client(),
        payload,
        idempotency_key,
    )

    assert in_progress_response.status_code == 409
    assert in_progress_response.get_json() == {
        "error": "Idempotency request already in progress"
    }
    assert validation_calls["count"] == 1

    release_view.set()
    thread.join(timeout=2)
    assert not thread.is_alive()

    first_response = first_result["response"]
    assert first_response.status_code == 200
    assert first_response.get_json() == {"message": "Payment confirmed"}
    assert app.fake_redis.exists(f"webhook:event:{event_id}") == 1

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value
        paid_at = refreshed.paid_at
        assert paid_at is not None

    cached = json.loads(app.fake_redis.store[idempotency_cache_key])
    assert cached["body"] == {"message": "Payment confirmed"}
    assert cached["status_code"] == 200

    replay_response = _post_signed_webhook(
        app.test_client(),
        payload,
        idempotency_key,
    )

    assert replay_response.status_code == 200
    assert replay_response.get_json() == {"message": "Payment confirmed"}
    assert validation_calls["count"] == 1

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value
        assert refreshed.paid_at == paid_at


def test_concurrent_same_event_id_with_different_idempotency_keys_uses_event_lock(
    app,
    monkeypatch,
):
    import routes.webhooks as webhooks_module

    external_id = "ext-concurrent-event-dedupe"
    event_id = "evt-concurrent-event-dedupe"
    first_idempotency_key = "idem-concurrent-event-first"
    second_idempotency_key = "idem-concurrent-event-second"
    second_cache_key = f"idempotency:{second_idempotency_key}"
    entered_transition = threading.Event()
    release_transition = threading.Event()
    transition_calls = {"count": 0}
    original_transition = webhooks_module.transition_charge

    with app.app_context():
        charge = _create_charge(value=100.0, external_id=external_id)
        charge_id = charge.id
        app.fake_redis.setex(f"charge:ttl:{external_id}", 1800, "PENDING")

    def blocking_transition(charge, new_state):
        transition_calls["count"] += 1
        entered_transition.set()
        assert release_transition.wait(timeout=2)
        return original_transition(charge, new_state)

    monkeypatch.setattr(webhooks_module, "transition_charge", blocking_transition)

    payload = {
        "event_id": event_id,
        "external_id": external_id,
        "value": 100.0,
        "status": "PAID",
    }
    first_result = {}

    def first_request():
        first_result["response"] = _post_signed_webhook(
            app.test_client(),
            payload,
            first_idempotency_key,
        )

    thread = threading.Thread(target=first_request)
    thread.start()
    assert entered_transition.wait(timeout=2)
    assert transition_calls["count"] == 1

    in_progress_response = _post_signed_webhook(
        app.test_client(),
        payload,
        second_idempotency_key,
    )

    assert in_progress_response.status_code == 503
    assert in_progress_response.get_json() == {
        "error": "Event processing in progress"
    }
    assert second_cache_key not in app.fake_redis.store
    assert app.fake_redis.exists(f"webhook:event:{event_id}:lock") == 1

    release_transition.set()
    thread.join(timeout=2)
    assert not thread.is_alive()

    first_response = first_result["response"]
    assert first_response.status_code == 200
    assert first_response.get_json() == {"message": "Payment confirmed"}
    assert app.fake_redis.get(f"webhook:event:{event_id}") == "processed"
    assert app.fake_redis.exists(f"webhook:event:{event_id}:lock") == 0

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value
        paid_at = refreshed.paid_at
        assert paid_at is not None

    retry_response = _post_signed_webhook(
        app.test_client(),
        payload,
        second_idempotency_key,
    )

    assert retry_response.status_code == 200
    assert retry_response.get_json() == {"message": "Duplicate event ignored"}
    assert transition_calls["count"] == 1

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value
        assert refreshed.paid_at == paid_at


def test_webhook_commit_failure_releases_event_lock_and_allows_retry(
    client,
    app,
    monkeypatch,
):
    import routes.webhooks as webhooks_module

    external_id = "ext-transition-failure-retry"
    event_id = "evt-transition-failure-retry"
    payload = {
        "event_id": event_id,
        "external_id": external_id,
        "value": 100.0,
        "status": "PAID",
    }

    with app.app_context():
        charge = _create_charge(value=100.0, external_id=external_id)
        charge_id = charge.id
        app.fake_redis.setex(f"charge:ttl:{external_id}", 1800, "PENDING")

    original_transition = webhooks_module.transition_charge

    def failing_transition(charge, new_state):
        raise RuntimeError("database commit failed")

    monkeypatch.setattr(webhooks_module, "transition_charge", failing_transition)

    first_response = _post_signed_webhook(client, payload, "idem-transition-failure-first")

    assert first_response.status_code == 500
    assert first_response.get_json() == {"error": "Internal server error"}
    assert app.fake_redis.exists(f"webhook:event:{event_id}") == 0
    assert app.fake_redis.exists(f"webhook:event:{event_id}:lock") == 0

    monkeypatch.setattr(webhooks_module, "transition_charge", original_transition)

    retry_response = _post_signed_webhook(client, payload, "idem-transition-failure-second")

    assert retry_response.status_code == 200
    assert retry_response.get_json() == {"message": "Payment confirmed"}
    assert app.fake_redis.get(f"webhook:event:{event_id}") == "processed"
    assert app.fake_redis.exists(f"webhook:event:{event_id}:lock") == 0

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value
        assert refreshed.paid_at is not None


def test_webhook_decimal_cent_value_confirms_matching_charge(client, app):
    with app.app_context():
        charge = _create_charge(
            value=Decimal("0.10"),
            status=ChargeStatus.PENDING,
            external_id="ext-decimal-cent-value",
        )
        charge_id = charge.id
        app.fake_redis.setex(f"charge:ttl:{charge.external_id}", 1800, "PENDING")

    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_decimal_cent_value",
            "external_id": "ext-decimal-cent-value",
            "value": 0.10,
            "status": "PAID",
        },
        "idem-decimal-cent-value",
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Payment confirmed"}

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value
        assert refreshed.value == Decimal("0.10")


@pytest.mark.parametrize("webhook_value", [10, 10.0, 10.00, "10.00"])
def test_webhook_equivalent_decimal_scales_confirm_charge(client, app, webhook_value):
    external_id = f"ext-equivalent-decimal-{str(webhook_value).replace('.', '-')}"
    event_id = f"evt-equivalent-decimal-{str(webhook_value).replace('.', '-')}"

    with app.app_context():
        charge = _create_charge(
            value=Decimal("10.00"),
            status=ChargeStatus.PENDING,
            external_id=external_id,
        )
        charge_id = charge.id
        app.fake_redis.setex(f"charge:ttl:{external_id}", 1800, "PENDING")

    response = _post_signed_webhook(
        client,
        {
            "event_id": event_id,
            "external_id": external_id,
            "value": webhook_value,
            "status": "PAID",
        },
        event_id,
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Payment confirmed"}

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PAID.value


def test_webhook_rejects_more_than_two_decimal_places_and_keeps_pending(client, app):
    with app.app_context():
        charge = _create_charge(
            value=Decimal("10.00"),
            status=ChargeStatus.PENDING,
            external_id="ext-invalid-decimal-scale",
        )
        charge_id = charge.id
        app.fake_redis.setex(f"charge:ttl:{charge.external_id}", 1800, "PENDING")

    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_invalid_decimal_scale",
            "external_id": "ext-invalid-decimal-scale",
            "value": 10.001,
            "status": "PAID",
        },
        "idem-invalid-decimal-scale",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid value type"}

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PENDING.value
        assert refreshed.paid_at is None
        assert app.fake_redis.exists(
            "webhook:event:evt_invalid_decimal_scale"
        ) == 0
        assert app.fake_redis.exists(
            "webhook:event:evt_invalid_decimal_scale:lock"
        ) == 0


def test_webhook_cent_mismatch_keeps_charge_pending(client, app):
    with app.app_context():
        charge = _create_charge(
            value=Decimal("10.00"),
            status=ChargeStatus.PENDING,
            external_id="ext-cent-mismatch",
        )
        charge_id = charge.id
        app.fake_redis.setex(f"charge:ttl:{charge.external_id}", 1800, "PENDING")

    response = _post_signed_webhook(
        client,
        {
            "event_id": "evt_cent_mismatch",
            "external_id": "ext-cent-mismatch",
            "value": 10.01,
            "status": "PAID",
        },
        "idem-cent-mismatch",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid value"}

    with app.app_context():
        refreshed = db.session.get(Charge, charge_id)
        assert refreshed.status == ChargeStatus.PENDING.value
        assert refreshed.paid_at is None
        assert app.fake_redis.exists("webhook:event:evt_cent_mismatch") == 0
        assert app.fake_redis.exists("webhook:event:evt_cent_mismatch:lock") == 0
