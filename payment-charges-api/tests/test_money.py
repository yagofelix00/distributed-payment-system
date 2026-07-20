from decimal import Decimal

import pytest

from services.money import (
    InvalidMoneyValue,
    MAX_MONEY_INPUT_LENGTH,
    money_to_json_number,
    parse_money,
)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (1, Decimal("1.00")),
        (1.0, Decimal("1.00")),
        (1.23, Decimal("1.23")),
        ("1", Decimal("1.00")),
        ("1.2", Decimal("1.20")),
        ("1.23", Decimal("1.23")),
        (Decimal("1.23"), Decimal("1.23")),
        ("9999999999.99", Decimal("9999999999.99")),
        (0.1, Decimal("0.10")),
    ],
)
def test_parse_money_accepts_valid_values(raw_value, expected):
    assert parse_money(raw_value) == expected


@pytest.mark.parametrize(
    "raw_value",
    [
        0,
        -1,
        None,
        True,
        False,
        "",
        "abc",
        "1.234",
        1.234,
        "NaN",
        "Infinity",
        "-Infinity",
        "10000000000.00",
    ],
)
def test_parse_money_rejects_invalid_values(raw_value):
    with pytest.raises(InvalidMoneyValue):
        parse_money(raw_value)


def test_parse_money_rejects_oversized_money_string_before_decimal_parsing():
    oversized_value = "1" * (MAX_MONEY_INPUT_LENGTH + 1)

    with pytest.raises(InvalidMoneyValue):
        parse_money(oversized_value)


def test_money_to_json_number_serializes_decimal_as_number():
    assert money_to_json_number(Decimal("19.99")) == 19.99
    assert isinstance(money_to_json_number(Decimal("19.99")), float)
