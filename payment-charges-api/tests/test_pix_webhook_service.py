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


@pytest.mark.parametrize(
    ("received_value", "expected_value"),
    [
        pytest.param(100, Decimal("100.00"), id="int-vs-decimal-cents"),
        pytest.param(100.0, Decimal("100.00"), id="float-integer-vs-decimal-cents"),
        pytest.param(
            Decimal("100.00"),
            Decimal("100.00"),
            id="decimal-cents-vs-decimal-cents",
        ),
        pytest.param("100", Decimal("100.00"), id="string-integer-vs-decimal-cents"),
        pytest.param(
            "100.0",
            Decimal("100.00"),
            id="string-one-decimal-vs-decimal-cents",
        ),
        pytest.param("100.00", Decimal("100.00"), id="string-cents-vs-decimal-cents"),
        pytest.param(0.1, Decimal("0.10"), id="float-cent-vs-decimal-cent"),
        pytest.param("0.01", Decimal("0.01"), id="minimum-string-cent"),
        pytest.param(Decimal("0.01"), Decimal("0.01"), id="minimum-decimal-cent"),
        pytest.param(
            "9999999999.99",
            Decimal("9999999999.99"),
            id="max-money-boundary",
        ),
        pytest.param(100, "100.00", id="int-vs-string-cents"),
        pytest.param("100.00", 100, id="string-cents-vs-int"),
        pytest.param(
            Decimal("100.00"),
            100.0,
            id="decimal-cents-vs-float-integer",
        ),
    ],
)
def test_validate_payment_value_accepts_equivalent_money_representations(
    received_value, expected_value
):
    assert validate_payment_value(received_value, expected_value) == "valid"


@pytest.mark.parametrize(
    ("received_value", "expected_value"),
    [
        pytest.param(100, Decimal("100.01"), id="integer-vs-one-cent-above"),
        pytest.param("99.99", Decimal("100.00"), id="string-one-cent-below"),
        pytest.param(
            Decimal("0.01"),
            Decimal("0.02"),
            id="minimum-cent-difference",
        ),
        pytest.param(
            9999999999.98,
            Decimal("9999999999.99"),
            id="near-max-cent-difference",
        ),
    ],
)
def test_validate_payment_value_returns_mismatch_for_valid_different_values(
    received_value, expected_value
):
    assert validate_payment_value(received_value, expected_value) == "mismatch"


@pytest.mark.parametrize(
    "received_value",
    [
        pytest.param(None, id="none"),
        pytest.param(True, id="bool-true"),
        pytest.param(False, id="bool-false"),
        pytest.param("", id="empty-string"),
        pytest.param("not-a-number", id="non-numeric-text"),
        pytest.param("NaN", id="nan-string"),
        pytest.param(float("nan"), id="nan-float"),
        pytest.param("Infinity", id="infinity-string"),
        pytest.param(float("inf"), id="infinity-float"),
        pytest.param("-Infinity", id="negative-infinity-string"),
        pytest.param(float("-inf"), id="negative-infinity-float"),
        pytest.param(0, id="zero-int"),
        pytest.param("0", id="zero-string"),
        pytest.param(Decimal("0.00"), id="zero-decimal"),
        pytest.param(-1, id="negative-int"),
        pytest.param("-0.01", id="negative-string-cent"),
        pytest.param("100.001", id="too-many-decimals-string"),
        pytest.param(100.001, id="too-many-decimals-float"),
        pytest.param(Decimal("100.001"), id="too-many-decimals-decimal"),
        pytest.param("10000000000.00", id="above-max-money"),
        pytest.param([], id="list"),
        pytest.param({}, id="dict"),
        pytest.param(object(), id="arbitrary-object"),
    ],
)
def test_validate_payment_value_rejects_invalid_received_values(received_value):
    assert validate_payment_value(received_value, Decimal("100.00")) == "invalid_type"


@pytest.mark.parametrize(
    "expected_value",
    [
        pytest.param(None, id="none"),
        pytest.param(True, id="bool-true"),
        pytest.param("NaN", id="nan-string"),
        pytest.param(0, id="zero-int"),
        pytest.param(-1, id="negative-int"),
        pytest.param("100.001", id="too-many-decimals-string"),
        pytest.param("10000000000.00", id="above-max-money"),
    ],
)
def test_validate_payment_value_rejects_invalid_expected_values(expected_value):
    assert validate_payment_value(Decimal("100.00"), expected_value) == "invalid_type"


def test_validate_payment_value_rejects_float_binary_artifact():
    # Do not silently round float artifacts with more than two visible decimals.
    assert str(0.1 + 0.2) == "0.30000000000000004"
    assert validate_payment_value(0.1 + 0.2, Decimal("0.30")) == "invalid_type"
