import hmac
import hashlib
import time
from flask import request, current_app, jsonify
from functools import wraps

# Maximum allowed time difference (in seconds) between
# the webhook event timestamp and the server time.
# This protects against replay attacks.
TOLERANCE_SECONDS = 300  # 5 minutes


def build_signed_message(timestamp_text: str, raw_body: bytes) -> bytes:
    """
    Build the exact byte sequence authenticated by webhook HMAC signatures.

    The timestamp is the literal X-Timestamp header value. It must not be
    normalized before signing; validation of its numeric format happens after
    the timestamp/body pair has been authenticated.
    """
    return timestamp_text.encode("utf-8") + b"." + raw_body


def calculate_signature(secret: bytes, timestamp_text: str, raw_body: bytes) -> str:
    """
    Calculate the expected sha256-prefixed HMAC signature for a webhook.
    """
    digest = hmac.new(
        secret,
        build_signed_message(timestamp_text, raw_body),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def _is_valid_timestamp_format(timestamp_text: str) -> bool:
    return (
        bool(timestamp_text)
        and timestamp_text.isascii()
        and timestamp_text.isdigit()
    )


def verify_webhook_signature():
    """
    Verifies the authenticity and freshness of a webhook request.

    Security checks:
    - Validates the presence of required headers
    - Authenticates the literal X-Timestamp value and raw request body together
    - Protects against replay attacks using a timestamp tolerance window
    - Validates the HMAC signature using the raw request body byte-for-byte
    """

    signature = request.headers.get("X-Signature")
    timestamp_text = request.headers.get("X-Timestamp")

    # Required headers must be present
    if not signature or not timestamp_text:
        return False

    # Raw request body must be used for signature validation
    raw_body = request.get_data()
    secret = current_app.config["WEBHOOK_SECRET"].encode("utf-8")

    # Authenticate the literal timestamp header together with the raw body.
    expected_signature = calculate_signature(secret, timestamp_text, raw_body)

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(signature, expected_signature):
        return False

    # ⏱ Replay attack protection
    # Reject requests outside the allowed time window after the timestamp/body
    # pair has been authenticated. The header format is intentionally strict so
    # equivalent-looking values such as '+1700000000' or '1700000000.0' fail.
    if not _is_valid_timestamp_format(timestamp_text):
        return False

    now = int(time.time())
    try:
        timestamp = int(timestamp_text)
    except ValueError:
        return False

    if abs(now - timestamp) > TOLERANCE_SECONDS:
        return False

    return True


def require_webhook_signature(f):
    """
    Flask decorator that enforces webhook signature validation.
    Rejects the request if the signature is invalid or missing.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        if not verify_webhook_signature():
            return jsonify({"error": "Invalid webhook signature"}), 401
        return f(*args, **kwargs)

    return decorated
