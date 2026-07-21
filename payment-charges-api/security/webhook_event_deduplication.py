import uuid

from audit.logger import logger
from infrastructure.redis_client import redis_client


EVENT_PROCESSED_TTL_SECONDS = 86400
EVENT_LOCK_TTL_SECONDS = 60
EVENT_PROCESSED_VALUE = "processed"

EVENT_CLAIM_ACQUIRED = "acquired"
EVENT_CLAIM_PROCESSED = "processed"
EVENT_CLAIM_PROCESSING = "processing"
EVENT_CLAIM_UNAVAILABLE = "unavailable"
EVENT_CLAIM_LOST_OWNERSHIP = "lost_ownership"
EVENT_CLAIM_MISSING = "missing"
EVENT_CLAIM_UNKNOWN = "unknown"


def _log_exception(message, event_id):
    try:
        logger.exception(message, extra={"event_id": event_id})
    except RuntimeError:
        # Unit tests and non-request contexts may not have Flask request context
        # available for the audit logger request_id processor. Logging must not
        # turn a controlled Redis failure into an unexpected exception.
        pass


def event_key(event_id):
    return f"webhook:event:{event_id}"


def event_lock_key(event_id):
    return f"webhook:event:{event_id}:lock"


def get_event_claim_state(event_id):
    try:
        if redis_client.exists(event_key(event_id)):
            return EVENT_CLAIM_PROCESSED

        if redis_client.exists(event_lock_key(event_id)):
            return EVENT_CLAIM_PROCESSING

        return EVENT_CLAIM_MISSING
    except Exception:
        _log_exception("Redis check failed for webhook event claim state", event_id)
        return EVENT_CLAIM_UNKNOWN


def acquire_event_claim(event_id, lock_ttl=EVENT_LOCK_TTL_SECONDS):
    try:
        if redis_client.exists(event_key(event_id)):
            return EVENT_CLAIM_PROCESSED, None

        token = str(uuid.uuid4())
        if redis_client.set(event_lock_key(event_id), token, nx=True, ex=lock_ttl):
            if redis_client.exists(event_key(event_id)):
                release_event_claim(event_id, token)
                return EVENT_CLAIM_PROCESSED, None
            return EVENT_CLAIM_ACQUIRED, token

        if redis_client.exists(event_key(event_id)):
            return EVENT_CLAIM_PROCESSED, None

        if redis_client.exists(event_lock_key(event_id)):
            return EVENT_CLAIM_PROCESSING, None

        retry_token = str(uuid.uuid4())
        if redis_client.set(event_lock_key(event_id), retry_token, nx=True, ex=lock_ttl):
            if redis_client.exists(event_key(event_id)):
                release_event_claim(event_id, retry_token)
                return EVENT_CLAIM_PROCESSED, None
            return EVENT_CLAIM_ACQUIRED, retry_token

        if redis_client.exists(event_key(event_id)):
            return EVENT_CLAIM_PROCESSED, None

        return EVENT_CLAIM_PROCESSING, None
    except Exception:
        _log_exception("Redis claim failed for webhook event", event_id)
        return EVENT_CLAIM_UNAVAILABLE, None


def mark_event_processed(event_id, token, ttl=EVENT_PROCESSED_TTL_SECONDS):
    try:
        if redis_client.get(event_lock_key(event_id)) != token:
            return EVENT_CLAIM_LOST_OWNERSHIP

        redis_client.setex(event_key(event_id), ttl, EVENT_PROCESSED_VALUE)
        return EVENT_CLAIM_PROCESSED
    except Exception:
        _log_exception("Failed to mark webhook event processed", event_id)
        return EVENT_CLAIM_UNAVAILABLE


def release_event_claim(event_id, token):
    try:
        if redis_client.get(event_lock_key(event_id)) == token:
            redis_client.delete(event_lock_key(event_id))
    except Exception:
        # Releasing the event lock must not mask the original response or exception.
        pass
