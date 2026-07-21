import hmac
import hashlib
from config import Config


def build_signed_message(timestamp_text: str, raw_body: bytes) -> bytes:
    return timestamp_text.encode("utf-8") + b"." + raw_body


def sign_payload(timestamp_text: str, raw_body: bytes) -> str:
    signature = hmac.new(
        Config.WEBHOOK_SECRET.encode("utf-8"),
        build_signed_message(timestamp_text, raw_body),
        hashlib.sha256,
    ).hexdigest()

    return f"sha256={signature}"
