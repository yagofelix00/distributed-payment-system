from db_models.charges import Charge
from services.charge_state_machine import ChargeState


def resolve_charge_for_paid_webhook(external_id):
    charge = Charge.query.filter_by(external_id=external_id).first()

    if not charge:
        return "not_found", None

    if str(charge.status) in (ChargeState.PAID.value, ChargeState.EXPIRED.value):
        return "already_processed", charge

    return "ok", charge
