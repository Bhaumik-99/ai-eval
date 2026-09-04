import sqlite3


def get_db():
    """Get an in-memory SQLite database with pre-populated test data."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user'
        )
    """)
    conn.execute("INSERT INTO users VALUES (1, 'admin', 'hashed_admin_pass_xyz', 'admin')")
    conn.execute("INSERT INTO users VALUES (2, 'alice', 'hashed_alice_pass_abc', 'user')")
    conn.execute("INSERT INTO users VALUES (3, 'bob', 'hashed_bob_pass_def', 'user')")
    conn.commit()
    return conn


def authenticate_user(username: str, password: str):
    """Authenticate a user by username and password.
    
    Returns the user row if credentials are valid, None otherwise.
    
    VULNERABILITY: This function is susceptible to SQL injection attacks!
    The username and password are directly interpolated into the SQL string.
    """
    conn = get_db()
    cursor = conn.cursor()
    # BUG: SQL injection vulnerability — never use f-strings/string formatting in SQL!
    query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
    cursor.execute(query)
    user = cursor.fetchone()
    conn.close()
    return user


def get_user_by_id(user_id: int, requesting_user_role: str):
    """Get user data. Only admin role can access any user record.
    
    VULNERABILITY: The role-based access check is missing!
    Any authenticated user can fetch any other user's data.
    """
    conn = get_db()
    cursor = conn.cursor()
    # BUG: No authorization check — should verify requesting_user_role == 'admin'
    #      before allowing access to any user record
    cursor.execute("SELECT id, username, role FROM users WHERE id=?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result
