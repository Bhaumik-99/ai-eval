"""Visible tests for JWT Auth task."""
from app import create_token, decode_token


def test_roundtrip_valid_token():
    secret = "super-secret-key-123"
    token = create_token({"user_id": 42, "role": "admin"}, secret, expires_in_sec=60)
    claims = decode_token(token, secret)
    assert claims["user_id"] == 42
    assert claims["role"] == "admin"
    assert "exp" in claims
