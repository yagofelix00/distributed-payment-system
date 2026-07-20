from decimal import Decimal

import pytest
from flask import Flask

from db_models.charges import Charge, ChargeStatus
from exceptions.charge_exceptions import ChargeNotPayable
from repository.database import db
from services.charge_service import confirm_payment


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_confirm_payment_checks_status_before_parsing_value(app):
    with app.app_context():
        charge = Charge(
            value=Decimal("100.00"),
            status=ChargeStatus.PAID.value,
            external_id="ext-paid-invalid-confirm-value",
        )
        db.session.add(charge)
        db.session.commit()

        with pytest.raises(ChargeNotPayable):
            confirm_payment(charge, "abc")

        refreshed = db.session.get(Charge, charge.id)
        assert refreshed.status == ChargeStatus.PAID.value
