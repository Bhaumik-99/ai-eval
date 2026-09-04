"""Visible tests for the Database Constraint task.
These tests are shown to the agent as a hint.
"""
import sqlite3
from app import init_db, create_user, get_user_by_email


def test_create_user_succeeds():
    """Creating a new user should return a positive integer ID."""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    user_id = create_user("Alice", "alice@example.com", conn)
    assert user_id is not None
    assert user_id > 0


def test_can_retrieve_created_user():
    """After creation, a user should be retrievable by email."""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    create_user("Alice", "alice@example.com", conn)
    user = get_user_by_email("alice@example.com", conn)
    assert user is not None
