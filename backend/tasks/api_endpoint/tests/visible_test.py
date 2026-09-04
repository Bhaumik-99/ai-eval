"""Visible tests for the API Endpoint task.
These tests are shown to the agent as a hint.
"""
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


def test_get_all_users():
    """GET /users should return the full user list."""
    response = client.get("/users")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
