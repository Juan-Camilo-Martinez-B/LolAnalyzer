import uuid

from fastapi.testclient import TestClient

from app.main import app


def _register(client: TestClient) -> str:
    email = f"recover_{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "username": "RecoverUser",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "favorite_champion": "Ahri",
            "peak_elo": "oro",
            "first_main": "Lee Sin",
        },
    )
    assert response.status_code == 201
    return email


def test_recovery_options_do_not_include_answers():
    with TestClient(app) as client:
        response = client.get("/api/auth/recovery-options")
        assert response.status_code == 200
        body = response.json()
        assert "oro" in body["elos"]
        assert any(item["name"] == "Ahri" for item in body["champions"])
        assert "hash" not in response.text.lower()


def test_reset_password_with_correct_answers_and_rejects_wrong_ones():
    with TestClient(app) as client:
        email = _register(client)
        wrong = client.post(
            "/api/auth/reset-password",
            json={
                "email": email,
                "favorite_champion": "Jinx",
                "peak_elo": "oro",
                "first_main": "Lee Sin",
                "new_password": "NewPassword123!",
                "new_password_confirm": "NewPassword123!",
            },
        )
        assert wrong.status_code == 401

        unknown = client.post(
            "/api/auth/reset-password",
            json={
                "email": "missing@example.com",
                "favorite_champion": "Ahri",
                "peak_elo": "oro",
                "first_main": "Lee Sin",
                "new_password": "NewPassword123!",
                "new_password_confirm": "NewPassword123!",
            },
        )
        assert unknown.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]

        reset = client.post(
            "/api/auth/reset-password",
            json={
                "email": email,
                "favorite_champion": "ahri",
                "peak_elo": "Oro",
                "first_main": "lee sin",
                "new_password": "NewPassword123!",
                "new_password_confirm": "NewPassword123!",
            },
        )
        assert reset.status_code == 200

        old_login = client.post("/api/auth/login", json={"email": email, "password": "Password123!"})
        assert old_login.status_code == 401
        new_login = client.post("/api/auth/login", json={"email": email, "password": "NewPassword123!"})
        assert new_login.status_code == 200


def test_login_lockout_after_repeated_failures():
    with TestClient(app) as client:
        email = _register(client)
        for _ in range(5):
            response = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
            assert response.status_code == 401
        locked = client.post("/api/auth/login", json={"email": email, "password": "Password123!"})
        assert locked.status_code == 429
