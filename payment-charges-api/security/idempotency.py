from flask import request, jsonify, make_response
from functools import wraps
from infrastructure.redis_client import redis_client
import json


def _is_valid_status_code(status_code):
    return (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 100 <= status_code <= 599
    )


def idempotent(ttl=300):
    """
    Idempotency decorator using Redis as the response cache.

    Contract:
    - Client MUST send 'Idempotency-Key' header for mutating operations.
    - Same key within the TTL returns the same response payload, preventing duplicate side effects
      (e.g., creating the same charge twice).
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Idempotency key should be provided by the client (unique per intended operation)
            key = request.headers.get("Idempotency-Key")

            if not key:
                return jsonify({"error": "Idempotency-Key missing"}), 400

            redis_key = f"idempotency:{key}"

            # If we have a cached response, return it immediately (idempotent replay).
            # Legacy entries contain only the response body and are replayed as 200.
            cached = redis_client.get(redis_key)
            if cached:
                try:
                    cached_data = json.loads(cached)
                except json.JSONDecodeError:
                    cached_data = None
                else:
                    is_response_envelope = (
                        isinstance(cached_data, dict)
                        and "body" in cached_data
                        and "status_code" in cached_data
                    )

                    if is_response_envelope:
                        status_code = cached_data["status_code"]
                        if _is_valid_status_code(status_code):
                            return jsonify(cached_data["body"]), status_code

                        # Invalid response envelopes are cache misses and will be
                        # overwritten after the view executes successfully.
                        cached_data = None
                    else:
                        return jsonify(cached_data)

            # Execute the original handler (first-time request for this key)
            response = f(*args, **kwargs)

            # Normalize Flask responses:
            # - View functions may return (json, status), Response objects, etc.
            # - make_response ensures we always have a proper Response to inspect.
            flask_response = make_response(response)

            data = flask_response.get_json()

            # Store the response for a limited time:
            # - Prevents duplicate side effects within TTL
            # - Keeps Redis usage bounded
            redis_client.setex(
                redis_key,
                ttl,
                json.dumps({
                    "body": data,
                    "status_code": flask_response.status_code,
                })
            )

            return flask_response

        return wrapper
    return decorator


