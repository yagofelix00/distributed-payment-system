import json
import time
import requests
from security.hmac import sign_payload


def send_webhook(url, payload):
    body = json.dumps(payload)
    body_bytes = body.encode("utf-8")
    timestamp = str(int(time.time()))

    headers = {
        "Content-Type": "application/json",
        "X-Signature": sign_payload(timestamp, body_bytes),
        "X-Event-Id": payload["event_id"],
        "X-Timestamp": timestamp,
    }

    try:
        response = requests.post(
            url,
            data=body_bytes,
            headers=headers,
            timeout=5
        )
        response.raise_for_status()
    except Exception as e:
        print(f"[BANK] Webhook failed: {e}")
