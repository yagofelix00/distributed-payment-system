from audit.logger import logger
from db_models.charges import Charge
from infrastructure.redis_client import redis_client
from services.charge_state_machine import ChargeState


def resolve_charge_for_paid_webhook(external_id):
    charge = Charge.query.filter_by(external_id=external_id).first()

    if not charge:
        return "not_found", None

    if str(charge.status) in (ChargeState.PAID.value, ChargeState.EXPIRED.value):
        return "already_processed", charge

    return "ok", charge


def check_charge_ttl(external_id):
    ttl_key = f"charge:ttl:{external_id}"

    try:
        ttl_exists = redis_client.exists(ttl_key)
    except Exception:
        logger.exception(f"Redis check failed for ttl_key={ttl_key}")
        return "unavailable"

    return "present" if ttl_exists else "missing"
