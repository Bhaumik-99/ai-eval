import base64
import hashlib
import hmac
import json
import time
from typing import Dict


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64decode(s: str) -> bytes:
    padding = 4 - (len(s) % 4)
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def create_token(payload: Dict, secret: str, expires_in_sec: int = 3600) -> str:
    """Create a signed JWT token with exp timestamp."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload_copy = dict(payload)
    payload_copy["exp"] = int(time.time()) + expires_in_sec

    header_b64 = _b64encode(json.dumps(header).encode("utf-8"))
    payload_b64 = _b64encode(json.dumps(payload_copy).encode("utf-8"))
    msg = f"{header_b64}.{payload_b64}".encode("utf-8")

    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    sig_b64 = _b64encode(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_token(token: str, secret: str) -> Dict:
    """Decode and verify token signature and expiry."""
    # BUG: Does not verify signature properly or check exp expiration!
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token format")
    payload_data = json.loads(_b64decode(parts[1]).decode("utf-8"))
    return payload_data
