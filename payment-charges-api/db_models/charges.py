import uuid
from datetime import datetime
from enum import Enum
from repository.database import db

class ChargeStatus(str, Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    EXPIRED = "EXPIRED"

class Charge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    value = db.Column(
        db.Numeric(12, 2, asdecimal=True),
        nullable=False,
    )
    status = db.Column(db.String(20), default=ChargeStatus.PENDING)
    external_id = db.Column(db.String(36), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    paid_at = db.Column(db.DateTime, nullable=True)
