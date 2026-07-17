from decimal import Decimal

import pytest

from services.pix_webhook_service import check_charge_ttl, validate_payment_value


class RedisStub:
    def __init__(self, exists_result=None, exists_error=None):
        self.exists_result = exists_result
        self.exists_error = exists_error
        self.keys_checked = []

    def exists(self, key):
        self.keys_checked.append(key)
        if self.exists_error:
            raise self.exists_error
        return self.exists_result


def test_check_charge_ttl_returns_present_when_ttl_key_exists(monkeypatch):
    redis_stub = RedisStub(exists_result=1)
    monkeypatch.setattr("services.pix_webhook_service.redis_client", redis_stub)

    result = check_charge_ttl("ext-ttl-present")

    assert result == "present"
    assert redis_stub.keys_checked == ["charge:ttl:ext-ttl-present"]


def test_check_charge_ttl_returns_missing_when_ttl_key_does_not_exist(monkeypatch):
    redis_stub = RedisStub(exists_result=0)
    monkeypatch.setattr("services.pix_webhook_service.redis_client", redis_stub)

    result = check_charge_ttl("ext-ttl-missing")

    assert result == "missing"
    assert redis_stub.keys_checked == ["charge:ttl:ext-ttl-missing"]


def test_check_charge_ttl_returns_unavailable_when_redis_exists_raises(monkeypatch):
    redis_stub = RedisStub(exists_error=RuntimeError("Redis unavailable"))
    monkeypatch.setattr("services.pix_webhook_service.redis_client", redis_stub)
    monkeypatch.setattr(
        "services.pix_webhook_service.logger.exception",
        lambda *args, **kwargs: None,
    )

    result = check_charge_ttl("ext-ttl-unavailable")

    assert result == "unavailable"
    assert redis_stub.keys_checked == ["charge:ttl:ext-ttl-unavailable"]


def test_validate_payment_value_returns_valid_for_equivalent_values():
    assert validate_payment_value("100.00", 100.0) == "valid"


def test_validate_payment_value_returns_invalid_type_for_non_numeric_value():
    assert validate_payment_value("not-a-number", 100.0) == "invalid_type"


def test_validate_payment_value_returns_mismatch_for_different_values():
    assert validate_payment_value(999.0, 100.0) == "mismatch"


@pytest.mark.parametrize(
    ("received_value", "expected_value"),
    [
        (100, 100.0),
        (100.0, "100.00"),
        (Decimal("100.00"), 100),
        ("100.00", Decimal("100.0")),
    ],
)
def test_validate_payment_value_returns_valid_for_equivalent_numeric_types(
    received_value, expected_value
):
    assert validate_payment_value(received_value, expected_value) == "valid"
