from flask import Blueprint, request, jsonify
from repository.database import db
from infrastructure.redis_client import redis_client
from security.idempotency import idempotent
from audit.logger import logger
from security.webhook_signature import require_webhook_signature
from services.pix_webhook_service import (
    check_charge_ttl,
    resolve_charge_for_paid_webhook,
    validate_payment_value,
)
from services.charge_state_machine import (
    ChargeState,
    InvalidChargeTransition,
    transition_charge,
)

# Blueprint responsible for handling incoming payment webhooks
webhooks_bp = Blueprint("webhooks", __name__)


def validate_pix_webhook_payload(data):
    if not isinstance(data, dict):
        return None, ({"error": "Invalid JSON payload"}, 400)

    external_id = data.get("external_id")
    value = data.get("value")
    status = data.get("status")
    event_id = data.get("event_id")

    if not event_id:
        return None, ({"error": "event_id is required"}, 400)

    if not external_id or not value or not status:
        return None, ({"error": "Invalid payload"}, 400)

    if status != "PAID":
        return None, ({"message": "Ignored"}, 200)

    return {
        "event_id": event_id,
        "external_id": external_id,
        "value": value,
        "status": status,
    }, None


@webhooks_bp.route("/webhooks/pix", methods=["POST"])
@require_webhook_signature
@idempotent(ttl=300)
def pix_webhook():
    """
    PIX payment webhook endpoint.

    This endpoint is called by the bank (or fake bank service) to notify
    about payment status changes.

    Responsibilities:
    - Validate webhook authenticity (HMAC signature)
    - Prevent duplicated event processing (idempotency)
    - Validate payload integrity
    - Ensure charge is still valid using Redis TTL
    - Update payment status in the database
    """

    # Parse incoming JSON payload
    data = request.get_json(silent=True)

    payload, error = validate_pix_webhook_payload(data)
    if error:
        body, status_code = error
        return jsonify(body), status_code

    external_id = payload["external_id"]
    value = payload["value"]
    status = payload["status"]
    event_id = payload["event_id"]

    # Centralized safety guard: catch unexpected exceptions and ensure
    # we return a controlled 500 while logging the full stack trace.
    try:
        # 📤 2. Ignora notificações que não representam pagamento concluído
        # ✅ Dedupe only for PAID events (avoid blocking a later PAID for same event_id)
        event_key = f"webhook:event:{event_id}"
        try:
            if redis_client.exists(event_key):
                logger.info(
                    "Duplicate webhook event ignored",
                    extra={"event_id": event_id, "external_id": external_id}
                )
                return jsonify({"message": "Duplicate event ignored"}), 200
        except Exception:
            logger.exception(f"Redis check failed for event dedupe key={event_key}")
            return jsonify({"error": "Service unavailable"}), 503

        # 🔍 3. Busca charges
        charge_result, charge = resolve_charge_for_paid_webhook(external_id)

        if charge_result == "not_found":
            logger.error(f"Charge not found | external_id={external_id}")
            return jsonify({"error": "Charge not found"}), 404

        if charge_result == "already_processed":
            logger.info(f"Ignored webhook for already finalized charge | id={charge.id} | status={charge.status}")
            return jsonify({"message": "Charge already processed"}), 200

        ttl_result = check_charge_ttl(external_id)

        if ttl_result == "unavailable":
            return jsonify({"error": "Service unavailable"}), 503

        if ttl_result == "missing":
            try:
                logger.warning(f"Webhook received but charge TTL missing/expired | id={charge.id}")
                return jsonify({"message": "Expired charge ignored"}), 200

            except InvalidChargeTransition:
                logger.warning(
                    f"Ignored webhook for non-pending charge | id={charge.id}"
                )
                return jsonify({"message": "Charge already processed"}), 200
            except Exception:
                logger.exception(f"Failed to mark charge expired | id={charge.id}")
                return jsonify({"error": "Internal server error"}), 500
      
        # ...
        payment_value_result = validate_payment_value(value, charge.value)

        if payment_value_result == "invalid_type":
            return jsonify({"error": "Invalid value type"}), 400

        if payment_value_result == "mismatch":
            logger.warning(
                f"Invalid value on webhook | charge_id={charge.id} | "
                f"got={value} expected={charge.value}"
            )
            return jsonify({"error": "Invalid value"}), 400


        try:
            transition_charge(charge, ChargeState.PAID)
        except InvalidChargeTransition:
            logger.warning(f"Ignored webhook for non-pending charge | id={charge.id}")
            return jsonify({"message": "Charge already processed"}), 200
        except Exception:
            logger.exception(f"Failed to commit payment for charge | id={charge.id}")
            return jsonify({"error": "Internal server error"}), 500
        
        # Mark event as processed only after successful state transition
        try:
            redis_client.setex(event_key, 86400, "1")
        except Exception:
            logger.exception(
                "Failed to persist webhook dedupe key after successful processing",
                extra={"event_id": event_id, "external_id": external_id}
            )

        # Log informativo para auditoria / monitoramento.
        logger.info(
            f"Payment confirmed via webhook", 
            extra={"charge_id": charge.id, "external_id": external_id}
        )

        return jsonify({"message": "Payment confirmed"}), 200

    except Exception:
        # Fallback: log completo e resposta genérica. Não vaza detalhes.
        logger.exception("Unhandled error processing PIX webhook")
        return jsonify({"error": "Internal server error"}), 500
