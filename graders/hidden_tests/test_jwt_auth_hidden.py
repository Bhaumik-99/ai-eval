"""
HIDDEN GRADER — JWT Token Verification & Expiry
Tests tampering, wrong secrets, expired tokens, and malformed structures.
"""
import pytest
import time


def test_tampered_payload_rejected():
    """Modifying the payload without resigning must raise ValueError."""
    from app import create_token, decode_token, _b64encode
    import json
    secret = "my-secret-key"
    token = create_token({"user_id": 1, "role": "user"}, secret, expires_in_sec=300)
    parts = token.split(".")
    # Tamper payload to elevate privilege to admin
    tampered_payload = _b64encode(json.dumps({"user_id": 1, "role": "admin", "exp": 9999999999}).encode("utf-8"))
    tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

    with pytest.raises(ValueError):
        decode_token(tampered_token, secret)


def test_wrong_secret_rejected():
    """Decoding with the wrong secret key must fail signature verification."""
    from app import create_token, decode_token
    token = create_token({"user": "alice"}, "correct-secret", expires_in_sec=300)
    with pytest.raises(ValueError):
        decode_token(token, "wrong-secret")


def test_expired_token_rejected():
    """Expired token must raise ValueError."""
    from app import create_token, decode_token
    token = create_token({"user": "bob"}, "my-secret", expires_in_sec=-10)
    with pytest.raises(ValueError):
        decode_token(token, "my-secret")


def test_malformed_token_rejected():
    """Non-three-part token string must raise ValueError."""
    from app import decode_token
    with pytest.raises(ValueError):
        decode_token("not-a-valid-token", "secret")
    with pytest.raises(ValueError):
        decode_token("header.payload", "secret")


def test_token_format_standard():
    """Generated token must have 3 dot-separated segments."""
    from app import create_token
    tok = create_token({"a": 1}, "sec")
    parts = tok.split(".")
    assert len(parts) == 3
