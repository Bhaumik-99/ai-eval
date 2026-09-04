"""
HIDDEN GRADER — Email Uniqueness Constraint
These tests are NOT shown to the agent.
"""
import pytest
import sqlite3


def test_duplicate_email_raises():
    """Inserting the same email twice must raise an exception."""
    from app import init_db, create_user
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    create_user("Alice", "alice@example.com", conn)
    with pytest.raises(Exception):
        create_user("Alice Two", "alice@example.com", conn)


def test_different_emails_both_succeed():
    """Two users with different emails must both be created successfully."""
    from app import init_db, create_user
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    id1 = create_user("Alice", "alice@example.com", conn)
    id2 = create_user("Bob", "bob@example.com", conn)
    assert id1 != id2
    assert id1 > 0
    assert id2 > 0


def test_empty_email_raises():
    """Empty string email must raise an exception."""
    from app import init_db, create_user
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    with pytest.raises(Exception):
        create_user("Alice", "", conn)


def test_three_users_unique_emails():
    """Three users with distinct emails should all be stored."""
    from app import init_db, create_user, get_all_users
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    create_user("Alice", "alice@example.com", conn)
    create_user("Bob", "bob@example.com", conn)
    create_user("Charlie", "charlie@example.com", conn)
    users = get_all_users(conn)
    assert len(users) == 3


def test_constraint_is_at_db_level():
    """The unique constraint must be at the DB level (schema), not just app logic."""
    from app import init_db
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO users (name, email) VALUES (?, ?)", ("Alice", "alice@example.com"))
    conn.commit()
    with pytest.raises(Exception):
        conn.execute("INSERT INTO users (name, email) VALUES (?, ?)", ("Alice2", "alice@example.com"))
        conn.commit()
