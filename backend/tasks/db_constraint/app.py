import sqlite3
from typing import Optional, Tuple


def init_db(conn: sqlite3.Connection) -> None:
    """Initialize the database schema.
    
    BUG: The email column does NOT have a UNIQUE constraint!
    Duplicate emails can be inserted without error.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT    NOT NULL,
            email TEXT   NOT NULL
        )
    """)
    conn.commit()


def create_user(name: str, email: str, conn: Optional[sqlite3.Connection] = None) -> int:
    """Create a new user in the database.
    
    Args:
        name: User's display name
        email: User's email address (should be unique — but currently NOT enforced)
        conn: Optional database connection (for testing)
    
    Returns:
        The new user's ID
    
    BUG: No uniqueness check — duplicate emails are silently accepted!
    """
    if conn is None:
        conn = sqlite3.connect(":memory:")
        init_db(conn)
    cursor = conn.cursor()
    # Missing: check for existing email before insert
    cursor.execute("INSERT INTO users (name, email) VALUES (?, ?)", (name, email))
    conn.commit()
    return cursor.lastrowid


def get_user_by_email(email: str, conn: sqlite3.Connection) -> Optional[Tuple]:
    """Fetch a user by their email address."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email=?", (email,))
    return cursor.fetchone()


def get_all_users(conn: sqlite3.Connection):
    """Return all users."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    return cursor.fetchall()
