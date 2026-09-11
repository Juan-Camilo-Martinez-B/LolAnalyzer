"""
Integration and unit tests for Summoner Profile API, LCU linkage, Password Change, and Secure Account Deletion.
"""

import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestProfileAPI:
    def test_profile_crud_and_account_lifecycle(self, client: TestClient):
        uid = uuid.uuid4().hex[:8]
        email = f"profile_tester_{uid}@example.com"

        # 1. Register a test user
        reg_payload = {
            "email": email,
            "username": "ProfileTester",
            "password": "Password123!",
            "region": "la1",
            "preferred_roles": "MID,ADC",
            "coach_sensitivity": "normal",
        }
        reg_res = client.post("/api/auth/register", json=reg_payload)
        assert reg_res.status_code == 201
        token = reg_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. GET /api/profile
        prof_res = client.get("/api/profile", headers=headers)
        assert prof_res.status_code == 200
        prof_data = prof_res.json()
        assert prof_data["email"] == email
        assert prof_data["username"] == "ProfileTester"
        assert prof_data["region"] == "la1"
        assert prof_data["preferred_roles"] == "MID,ADC"
        assert prof_data["coach_sensitivity"] == "normal"
        assert prof_data["total_matches_recorded"] == 0

        # 3. PUT /api/profile (Update editable profile fields)
        update_payload = {
            "username": "EliteMidLaner",
            "avatar_url": "https://example.com/avatar.png",
            "preferred_roles": "MID",
            "coach_sensitivity": "high",
            "region": "na1",
            "summoner_name": "FakerJunior",
        }
        update_res = client.put("/api/profile", json=update_payload, headers=headers)
        assert update_res.status_code == 200
        updated_data = update_res.json()
        assert updated_data["username"] == "EliteMidLaner"
        assert updated_data["avatar_url"] == "https://example.com/avatar.png"
        assert updated_data["preferred_roles"] == "MID"
        assert updated_data["coach_sensitivity"] == "high"
        assert updated_data["region"] == "na1"
        assert updated_data["summoner_name"] == "FakerJunior"

        # 4. POST /api/profile/unlink-lcu
        unlink_res = client.post("/api/profile/unlink-lcu", headers=headers)
        assert unlink_res.status_code == 200
        assert unlink_res.json()["summoner_name"] is None

        # 5. POST /api/profile/change-password - wrong current password
        bad_pw_res = client.post(
            "/api/profile/change-password",
            json={"current_password": "WrongPassword!", "new_password": "NewSecretPassword456!"},
            headers=headers,
        )
        assert bad_pw_res.status_code == 400

        # 6. POST /api/profile/change-password - correct current password
        good_pw_res = client.post(
            "/api/profile/change-password",
            json={"current_password": "Password123!", "new_password": "NewSecretPassword456!"},
            headers=headers,
        )
        assert good_pw_res.status_code == 200
        assert good_pw_res.json()["status"] == "password_changed"

        # 7. Verify login works with new password
        login_new = client.post(
            "/api/auth/login",
            json={"email": email, "password": "NewSecretPassword456!"},
        )
        assert login_new.status_code == 200
        new_token = login_new.json()["access_token"]
        new_headers = {"Authorization": f"Bearer {new_token}"}

        # 8. DELETE /api/profile/delete-account - Invalid confirmation text
        del_bad_confirm = client.request(
            "DELETE",
            "/api/profile/delete-account",
            json={"password": "NewSecretPassword456!", "confirmation_text": "CANCEL"},
            headers=new_headers,
        )
        assert del_bad_confirm.status_code == 400

        # 9. DELETE /api/profile/delete-account - Wrong password
        del_bad_pw = client.request(
            "DELETE",
            "/api/profile/delete-account",
            json={"password": "WrongPassword!", "confirmation_text": "DELETE"},
            headers=new_headers,
        )
        assert del_bad_pw.status_code == 401

        # 10. DELETE /api/profile/delete-account - Successful deletion
        del_success = client.request(
            "DELETE",
            "/api/profile/delete-account",
            json={"password": "NewSecretPassword456!", "confirmation_text": "DELETE"},
            headers=new_headers,
        )
        assert del_success.status_code == 200
        assert del_success.json()["status"] == "account_deleted"

        # 11. Verify token is now blacklisted/unauthorized
        del_verify = client.get("/api/profile", headers=new_headers)
        assert del_verify.status_code == 401

        # 12. Verify user cannot log in anymore
        relogin = client.post(
            "/api/auth/login",
            json={"email": email, "password": "NewSecretPassword456!"},
        )
        assert relogin.status_code == 401
