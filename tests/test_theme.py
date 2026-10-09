"""Theme preference stays on the account and rejects unknown values."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _register(client: TestClient) -> dict:
    email = f"theme_{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "username": "ThemeTester",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "favorite_champion": "Ahri",
            "peak_elo": "oro",
            "first_main": "Lee Sin",
        },
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_theme_defaults_to_light_and_can_switch(client: TestClient):
    headers = _register(client)

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["theme_preference"] == "light"

    updated = client.put(
        "/api/profile",
        headers=headers,
        json={"theme_preference": "dark"},
    )
    assert updated.status_code == 200
    assert updated.json()["theme_preference"] == "dark"

    again = client.get("/api/auth/me", headers=headers)
    assert again.json()["theme_preference"] == "dark"


def test_unknown_theme_is_rejected(client: TestClient):
    headers = _register(client)
    rejected = client.put(
        "/api/profile",
        headers=headers,
        json={"theme_preference": "neon"},
    )
    assert rejected.status_code == 422
