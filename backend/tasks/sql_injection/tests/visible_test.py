"""Visible tests for the SQL Injection task.
These tests are shown to the agent as a hint.
"""
from app import authenticate_user


def test_valid_login():
    """A valid username and password should authenticate successfully."""
    user = authenticate_user("alice", "hashed_alice_pass_abc")
    assert user is not None
    assert user[1] == "alice"  # username field


def test_invalid_password():
    """A wrong password should return None."""
    user = authenticate_user("alice", "wrong_password")
    assert user is None
