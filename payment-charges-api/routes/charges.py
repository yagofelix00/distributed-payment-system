from flask import Blueprint, request, jsonify
from db_models.charges import Charge
from infrastructure.redis_client import redis_client
import json
from extensions import limiter

from audit.logger import logger
from exceptions.charge_exceptions import InvalidChargeValue
from services.charge_service import create_charge as create_charge_service
from services.charge_state_machine import (
    ChargeState,
    InvalidChargeTransition,
    transition_charge,
)

charges_bp = Blueprint("charges", __name__, url_prefix="/payment")


@charges_bp.route("/charges", methods=["POST"])
@limiter.limit("10 per minute")
def create_charge():
    # NOTE: Keep HTTP layer thin: validate basic request shape and delegate business rules to service layer
    # (In this project, logic is still here for simplicity, but the intention is clear.)
    data = request.get_json()

    # Basic validation: ensure required fields exist and are valid
    if not data or "value" not in data:
        return jsonify({"error": "Value is required"}), 400

    try:
        charge = create_charge_service(data["value"])
    except InvalidChargeValue:
        return jsonify({"error": "Invalid value"}), 400

    return jsonify({
        "id": charge.id,
        "external_id": charge.external_id,
        "status": charge.status,
    }), 201


@charges_bp.route("/charges/<int:charge_id>", methods=["GET"])
def get_charge(charge_id):
    # Read-through caching: speed up repeated reads of the same charge for short periods.
    # IMPORTANT: Cache is treated as ephemeral — DB remains the persistent store.
    cache_key = f"charge:{charge_id}"

    cached = redis_client.get(cache_key)
    if cached:
        cached_response = json.loads(cached)
        # Finalized charges are safe to serve from cache. PENDING charges must still
        # validate the Redis TTL because the TTL key is the expiration authority.
        if cached_response.get("status") != ChargeState.PENDING.value:
            return jsonify(cached_response)

    charge = Charge.query.get(charge_id)
    if not charge:
        return jsonify({"error": "Charge not found"}), 404

    ttl_key = f"charge:ttl:{charge.external_id}"

    # Lazy expiration strategy:
    # If the TTL key no longer exists and the charge is still PENDING, we mark it EXPIRED.
    # This ensures the API reflects expiration without relying on background schedulers.
    if not redis_client.exists(ttl_key):
        logger.warning(f"TTL missing for charge | id={charge.id} | status={charge.status}")

        if charge.status == ChargeState.PENDING.value:
            try:
                transition_charge(charge, ChargeState.EXPIRED)
                # Invalidate cache (if any) to avoid serving stale state after status transition.
                redis_client.delete(cache_key)
                logger.info(f"Charge expired via TTL check | id={charge.id}")
            except Exception:
                logger.exception(f"Failed to expire charge via TTL check | id={charge.id}")

    response = {
        "id": charge.id,
        "value": charge.value,
        "status": charge.status,
        # Optional: expires_at could be derived if you store created_at + TTL, but Redis TTL is the authority here.
    }

    # Short TTL cache to reduce load under read bursts (e.g., polling clients).
    redis_client.setex(
        cache_key,
        60,  # cache for 60 seconds
        json.dumps(response),
    )

    return jsonify(response)

