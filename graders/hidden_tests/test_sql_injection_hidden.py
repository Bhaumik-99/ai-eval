"""
HIDDEN GRADER — SQL Injection Security
These tests are NOT shown to the agent.
"""
import pytest


def test_classic_or_injection_blocked():
    """Classic ' OR 1=1 -- injection must be blocked."""
    from app import authenticate_user
    result = authenticate_user("' OR 1=1 --", "anything")
    assert result is None, "SQL injection bypassed authentication! The query is not parameterized."


def test_tautology_injection_blocked():
    """admin' -- injection must return None."""
    from app import authenticate_user
    result = authenticate_user("admin' --", "")
    assert result is None


def test_union_injection_blocked():
    """UNION SELECT injection must be blocked."""
    from app import authenticate_user
    payload = "' UNION SELECT 1,'hacker','pass','admin' --"
    result = authenticate_user(payload, "")
    assert result is None


def test_valid_login_still_works():
    """Fixing injection must not break legitimate logins."""
    from app import authenticate_user
    user = authenticate_user("alice", "hashed_alice_pass_abc")
    assert user is not None


def test_invalid_credentials_rejected():
    """Wrong password must still return None after the fix."""
    from app import authenticate_user
    user = authenticate_user("alice", "wrong_password")
    assert user is None


def test_empty_credentials_rejected():
    """Empty username/password must return None."""
    from app import authenticate_user
    result = authenticate_user("", "")
    assert result is None


def test_authorization_admin_can_access_any_user():
    """An admin role should be able to fetch any user."""
    from app import get_user_by_id
    result = get_user_by_id(2, "admin")
    assert result is not None


def test_authorization_user_cannot_access_others():
    """A regular user role should NOT be able to fetch other users' data."""
    from app import get_user_by_id
    result = get_user_by_id(1, "user")
    assert result is None, "Non-admin role should not access arbitrary user records"
