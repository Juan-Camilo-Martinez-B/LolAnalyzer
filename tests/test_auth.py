"""
Integration and Unit tests for Authentication, Google OAuth, JWT Tokens, and Revocation Blacklist.
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestAuthentication:
    def test_register_and_login_flow(self, client: TestClient):
        # 1. Register
        register_payload = {
            "email": "caps@g2.com",
            "username": "Caps",
            "password": "supersecurepassword123",
            "summoner_name": "G2 Caps",
            "region": "euw1",
        }
        res = client.post("/api/auth/register", json=register_payload)
        assert res.status_code == 201
        data = res.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

        # 2. Duplicate registration fails
        res_dup = client.post("/api/auth/register", json=register_payload)
        assert res_dup.status_code == 400

        # 3. Login with correct password
        login_res = client.post(
            "/api/auth/login",
            json={"email": "caps@g2.com", "password": "supersecurepassword123"},
        )
        assert login_res.status_code == 200
        assert "access_token" in login_res.json()

        # 4. Login with wrong password fails
        bad_login = client.post(
            "/api/auth/login",
            json={"email": "caps@g2.com", "password": "wrongpassword"},
        )
        assert bad_login.status_code == 401

    def test_protected_me_endpoint(self, client: TestClient):
        # Register user
        client.post(
            "/api/auth/register",
            json={
                "email": "jankos@heretics.com",
                "username": "Jankos",
                "password": "firstbloodking123",
                "summoner_name": "Jankos",
                "region": "euw1",
            },
        )
        login_res = client.post(
            "/api/auth/login",
            json={"email": "jankos@heretics.com", "password": "firstbloodking123"},
        )
        token = login_res.json()["access_token"]

        # Call /me with valid token
        headers = {"Authorization": f"Bearer {token}"}
        me_res = client.get("/api/auth/me", headers=headers)
        assert me_res.status_code == 200
        user_data = me_res.json()
        assert user_data["email"] == "jankos@heretics.com"
        assert user_data["username"] == "Jankos"
        assert user_data["auth_provider"] == "local"

        # Call /me without token fails
        unauth_res = client.get("/api/auth/me")
        assert unauth_res.status_code == 401

    def test_google_oauth_flow(self, client: TestClient):
        mock_google_profile = {
            "email": "gumayusi@t1.gg",
            "name": "Lee Min-hyeong",
            "picture": "https://lh3.googleusercontent.com/a/mock-pic",
            "google_id": "google_oauth_sub_987654321",
        }

        with patch("app.api.auth.verify_google_id_token", return_value=mock_google_profile):
            # 1. First Google login auto-registers
            res = client.post("/api/auth/google", json={"id_token": "valid_mock_google_id_token"})
            assert res.status_code == 200
            data = res.json()
            assert "access_token" in data
            token = data["access_token"]

            # 2. Check /me reflects Google metadata
            me_res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me_res.status_code == 200
            user_data = me_res.json()
            assert user_data["email"] == "gumayusi@t1.gg"
            assert user_data["auth_provider"] == "google"
            assert user_data["avatar_url"] == "https://lh3.googleusercontent.com/a/mock-pic"

    def test_logout_and_token_revocation_blacklist(self, client: TestClient):
        # Register and login
        client.post(
            "/api/auth/register",
            json={
                "email": "keria@t1.gg",
                "username": "Keria",
                "password": "supportgod123",
            },
        )
        login_res = client.post(
            "/api/auth/login",
            json={"email": "keria@t1.gg", "password": "supportgod123"},
        )
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Verify access works before logout
        assert client.get("/api/auth/me", headers=headers).status_code == 200

        # Perform Logout
        logout_res = client.post("/api/auth/logout", headers=headers)
        assert logout_res.status_code == 200
        assert logout_res.json()["status"] == "logged_out"

        # Subsequent request with revoked token MUST fail with 401
        revoked_check = client.get("/api/auth/me", headers=headers)
        assert revoked_check.status_code == 401
        assert "revoked" in revoked_check.json()["detail"].lower()

    def test_refresh_token_rotation(self, client: TestClient):
        # Register and get tokens
        reg = client.post(
            "/api/auth/register",
            json={
                "email": "zeus@hle.kr",
                "username": "Zeus",
                "password": "toplaneking123",
            },
        )
        refresh_token = reg.json()["refresh_token"]

        # Call /refresh with refresh_token
        ref_res = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert ref_res.status_code == 200
        new_data = ref_res.json()
        assert "access_token" in new_data
        assert "refresh_token" in new_data
