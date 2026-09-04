"""
HIDDEN GRADER — GET /users/{id} API Endpoint
These tests are NOT shown to the agent.
"""
import pytest


def test_get_user_valid_id_1():
    """GET /users/1 should return Alice with status 200."""
    from fastapi.testclient import TestClient
    from app import app
    client = TestClient(app)
    response = client.get("/users/1")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["name"] == "Alice Johnson"


def test_get_user_valid_id_2():
    """GET /users/2 should return Bob with status 200."""
    from fastapi.testclient import TestClient
    from app import app
    client = TestClient(app)
    response = client.get("/users/2")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 2


def test_get_user_not_found_returns_404():
    """GET /users/9999 should return HTTP 404."""
    from fastapi.testclient import TestClient
    from app import app
    client = TestClient(app)
    response = client.get("/users/9999")
    assert response.status_code == 404


def test_get_user_response_has_required_fields():
    """Response must include id, name, email fields."""
    from fastapi.testclient import TestClient
    from app import app
    client = TestClient(app)
    response = client.get("/users/1")
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert "name" in data
    assert "email" in data


def test_invalid_id_string_returns_422():
    """GET /users/abc should return HTTP 422 Unprocessable Entity."""
    from fastapi.testclient import TestClient
    from app import app
    client = TestClient(app)
    response = client.get("/users/abc")
    assert response.status_code == 422


def test_existing_endpoint_still_works():
    """GET /users (all users) should still return a list."""
    from fastapi.testclient import TestClient
    from app import app
    client = TestClient(app)
    response = client.get("/users")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
