from flask import Blueprint, jsonify
from sqlalchemy import text
from repository.database import db
from infrastructure.redis_client import redis_client

health_bp = Blueprint("health", __name__)

@health_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@health_bp.route("/ready", methods=["GET"])
def ready():
    database_status = "ok"
    redis_status = "ok"

    try:
        # Use SQLAlchemy 2.x textual SQL execution for connectivity check.
        db.session.execute(text("SELECT 1"))
    except Exception:
        database_status = "failed"

    try:
        # Verify Redis is reachable and responsive.
        redis_client.ping()
    except Exception:
        redis_status = "failed"

    is_ready = database_status == "ok" and redis_status == "ok"
    response = {
        "status": "ready" if is_ready else "not_ready",
        "database": database_status,
        "redis": redis_status,
    }

    return jsonify(response), 200 if is_ready else 503
