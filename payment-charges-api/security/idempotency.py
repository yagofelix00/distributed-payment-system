from flask import request, jsonify, make_response
from functools import wraps
from infrastructure.redis_client import redis_client
import hashlib
import json
import uuid


_DEFAULT_LOCK_TTL_SECONDS = 30
_CACHE_MISS = "miss"
_CACHE_HIT = "hit"


def _is_valid_status_code(status_code):
    return (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 100 <= status_code <= 599
    )


def _is_valid_request_fingerprint(fingerprint):
    if not isinstance(fingerprint, str):
        return False

    prefix = "sha256:v1:"
    if not fingerprint.startswith(prefix):
        return False

    digest = fingerprint[len(prefix):]

    return (
        len(digest) == 64
        and all(char in "0123456789abcdef" for char in digest)
    )


def _request_fingerprint():
    fingerprint_source = b"\n".join([
        request.method.encode("utf-8"),
        request.path.encode("utf-8"),
        request.query_string,
        request.get_data(cache=True),
    ])
    return f"sha256:v1:{hashlib.sha256(fingerprint_source).hexdigest()}"


def _read_cached_response(redis_key, request_fingerprint):
    # If we have a cached response, return it immediately (idempotent replay).
    # Legacy entries contain only the response body and are replayed as 200.
    cached = redis_client.get(redis_key)
    if cached:
        try:
            cached_data = json.loads(cached)
        except json.JSONDecodeError:
            return _CACHE_MISS, None

        is_response_envelope = (
            isinstance(cached_data, dict)
            and "body" in cached_data
            and "status_code" in cached_data
        )

        if is_response_envelope:
            status_code = cached_data["status_code"]
            if _is_valid_status_code(status_code):
                has_fingerprint = "request_fingerprint" in cached_data

                if has_fingerprint:
                    cached_fingerprint = cached_data["request_fingerprint"]

                    if not _is_valid_request_fingerprint(cached_fingerprint):
                        return _CACHE_MISS, None

                    if cached_fingerprint != request_fingerprint:
                        return _CACHE_HIT, (
                            jsonify({
                                "error": "Idempotency-Key reused with different request"
                            }),
                            409,
                        )

                    return _CACHE_HIT, (jsonify(cached_data["body"]), status_code)

                return _CACHE_HIT, (jsonify(cached_data["body"]), status_code)

            return _CACHE_MISS, None

        return _CACHE_HIT, jsonify(cached_data)

    return _CACHE_MISS, None


def _acquire_lock(lock_key, token, lock_ttl):
    return redis_client.set(lock_key, token, nx=True, ex=lock_ttl)


def _release_lock_if_owner(lock_key, token):
    try:
        if redis_client.get(lock_key) == token:
            redis_client.delete(lock_key)
    except Exception:
        # Releasing the lock must not mask the original view response or exception.
        pass


def idempotent(ttl=300, lock_ttl=_DEFAULT_LOCK_TTL_SECONDS):
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
            request_fingerprint = _request_fingerprint()

            cache_status, cached_response = _read_cached_response(
                redis_key,
                request_fingerprint,
            )
            if cache_status != _CACHE_MISS:
                return cached_response

            lock_key = f"{redis_key}:lock"
            lock_token = str(uuid.uuid4())
            lock_acquired = _acquire_lock(lock_key, lock_token, lock_ttl)

            if not lock_acquired:
                cache_status, cached_response = _read_cached_response(
                    redis_key,
                    request_fingerprint,
                )
                if cache_status != _CACHE_MISS:
                    return cached_response

                return jsonify({
                    "error": "Idempotency request already in progress"
                }), 409

            try:
                cache_status, cached_response = _read_cached_response(
                    redis_key,
                    request_fingerprint,
                )
                if cache_status != _CACHE_MISS:
                    return cached_response

                # Execute the original handler (first-time request for this key)
                response = f(*args, **kwargs)

                # Normalize Flask responses:
                # - View functions may return (json, status), Response objects, etc.
                # - make_response ensures we always have a proper Response to inspect.
                flask_response = make_response(response)

                if flask_response.status_code >= 500:
                    return flask_response

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
                        "request_fingerprint": request_fingerprint,
                    })
                )

                return flask_response
            finally:
                _release_lock_if_owner(lock_key, lock_token)

        return wrapper
    return decorator


