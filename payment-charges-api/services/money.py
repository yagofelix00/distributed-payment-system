from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")
MAX_MONEY_AMOUNT = Decimal("9999999999.99")
MAX_MONEY_INPUT_LENGTH = 32


class InvalidMoneyValue(ValueError):
    pass


def parse_money(value):
    if isinstance(value, bool):
        raise InvalidMoneyValue("Invalid money value")

    money_text = str(value).strip()
    if len(money_text) > MAX_MONEY_INPUT_LENGTH:
        raise InvalidMoneyValue("Invalid money value")

    try:
        amount = Decimal(money_text)
    except (InvalidOperation, TypeError, ValueError):
        raise InvalidMoneyValue("Invalid money value")

    if not amount.is_finite():
        raise InvalidMoneyValue("Invalid money value")

    if amount <= 0:
        raise InvalidMoneyValue("Invalid money value")

    try:
        quantized = amount.quantize(CENT)
    except InvalidOperation:
        raise InvalidMoneyValue("Invalid money value")

    if amount != quantized:
        raise InvalidMoneyValue("Invalid money value")

    if quantized > MAX_MONEY_AMOUNT:
        raise InvalidMoneyValue("Invalid money value")

    return quantized


def money_to_json_number(value):
    return float(parse_money(value))
