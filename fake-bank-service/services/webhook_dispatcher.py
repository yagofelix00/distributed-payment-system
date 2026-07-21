import json
import time
import random
import requests

from audit.request_context import get_request_id
from security.hmac import sign_payload
from dlq.storage import enqueue_failed_webhook


# Defaults (you can move these to config.py if you prefer)
DEFAULT_TIMEOUT_SECONDS = 5
DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_DELAY_SECONDS = 1.0
DEFAULT_BACKOFF_MULTIPLIER = 2.0
DEFAULT_MAX_DELAY_SECONDS = 30.0
DEFAULT_JITTER_RATIO = 0.20  # 20% jitter to avoid thundering herd


def _sleep_with_jitter(base_delay: float) -> float:
    """
    Adds +/- jitter to the delay, to avoid retry spikes.
    """
    jitter = base_delay * DEFAULT_JITTER_RATIO
    delay = base_delay + random.uniform(-jitter, jitter)
    return max(0.0, delay)


def send_webhook(
    url: str,
    payload: dict,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay_seconds: float = DEFAULT_INITIAL_DELAY_SECONDS,
    backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER,
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
) -> bool:
    """
    Sends a webhook with:
    - HMAC signature over the RAW JSON body
    - X-Timestamp header
    - X-Event-Id header
    - X-Request-Id for cross-service correlation
    - Retry with exponential backoff on network errors and non-2xx responses

    Returns:
      True if delivered (2xx), False otherwise.
    """
    if not url:
        raise ValueError("Webhook URL is required")

    if not isinstance(payload, dict):
        raise ValueError("Payload must be a dict")

    event_id = payload.get("event_id")
    if not event_id:
        raise ValueError("payload['event_id'] is required for idempotency")

    # IMPORTANT: Use a stable JSON serialization (no pretty printing).
    # The exact bytes signed here must be the exact bytes sent in the request.
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    body_bytes = body.encode("utf-8")

    request_id = get_request_id()

    delay = float(initial_delay_seconds)
    last_headers = None

    last_status_code = None
    last_error = None
    last_response_body = None

    for attempt in range(1, max_retries + 1):
        timestamp = str(int(time.time()))
        signature = sign_payload(timestamp, body_bytes)  # returns "sha256=<hex>"
        headers = {
            "Content-Type": "application/json",
            "X-Signature": signature,
            "X-Timestamp": timestamp,
            "X-Event-Id": event_id,
            "X-Request-Id": request_id,
        }
        last_headers = headers

        try:
            resp = requests.post(
                url,
                data=body_bytes,
                headers=headers,
                timeout=timeout_seconds,
            )

            # Zerando erro de exception se teve response HTTP
            last_error = None

            if 200 <= resp.status_code < 300:
                print(
                    f"[BANK] webhook delivered | attempt={attempt} "
                    f"| status={resp.status_code} | event_id={event_id} | request_id={request_id}"
                )
                return True

            last_status_code = resp.status_code
            last_response_body = (resp.text or "")[:1000]

            # 4xx (exceto 429) -> erro permanente, não vale retry
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                print(
                    f"[BANK] webhook non-retryable | attempt={attempt} "
                    f"| status={last_status_code} | event_id={event_id} | request_id={request_id} "
                    f"| response_body={last_response_body}"
                )
                break

            print(
                f"[BANK] webhook failed | attempt={attempt} "
                f"| status={last_status_code} | event_id={event_id} | request_id={request_id} "
                f"| response_body={last_response_body}"
            )    

        except requests.RequestException as e:
            print(
                f"[BANK] webhook error | attempt={attempt} "
                f"| error={e} | event_id={event_id} | request_id={request_id}"
            )
            last_error = str(e)

        if attempt == max_retries:
            break

        sleep_for = _sleep_with_jitter(delay)
        time.sleep(sleep_for)
        delay = min(delay * backoff_multiplier, max_delay_seconds)

    # DLQ: não persistir assinatura
    safe_headers = {
        k: v
        for k, v in (last_headers or {}).items()
        if k.lower() != "x-signature"
    }

    enqueue_failed_webhook(
        url=url,
        payload=payload,
        headers=safe_headers,
        last_status_code=last_status_code,
        last_error=last_error,
        # se você adicionar no storage.py:
        # last_response_body=last_response_body,
    )

    print(
    f"[BANK] webhook permanently failed after retries -> DLQ "
    f"| event_id={event_id} | request_id={request_id} "
    f"| last_status={last_status_code} | last_error={last_error} "
    f"| last_response_body={last_response_body}"
    )

    return False

