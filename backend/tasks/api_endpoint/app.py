from fastapi import FastAPI, HTTPException
from typing import Optional

app = FastAPI(title="UserService API")

# In-memory user database
USERS_DB = {
    1: {"id": 1, "name": "Alice Johnson", "email": "alice@example.com", "role": "admin", "active": True},
    2: {"id": 2, "name": "Bob Smith", "email": "bob@example.com", "role": "user", "active": True},
    3: {"id": 3, "name": "Charlie Brown", "email": "charlie@example.com", "role": "user", "active": False},
}


@app.get("/users")
def get_all_users():
    """Return all users."""
    return list(USERS_DB.values())


# TODO: Add GET /users/{user_id} endpoint
# Requirements:
#   - Return the user with the given ID
#   - Return HTTP 404 if the user does not exist
#   - user_id must be a positive integer (FastAPI handles type validation)
#   - Response must include: id, name, email, role, active fields
