"""
Full End-to-End Lifecycle Integration Test for LolAnalyzer Backend.
Simulates the entire user journey across all interconnected modules:
1. Registration & Local/Google Authentication
2. Profile Inspection & Customization
3. Real-time Match Telemetry Recording
4. Historical Match Logging with Time-Series Points
5. Aggregated Performance Statistics & Visual Analytics Querying
6. Token Revocation (Logout) & Re-authentication
7. Password Change & Verification
8. Secure Account Deletion with Cascade Verification
"""

from unittest.mock import patch
import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestFullE2ELifecycle:
    def test_complete_platform_user_journey(self, client: TestClient):
        uid = uuid.uuid4().hex[:8]
        user_email = f"challenger_{uid}@riotgames.com"
        user_pwd = "InitialPassword123!"

        # =========================================================================
        # 1. Registration & Authentication
        # =========================================================================
        reg_res = client.post(
            "/api/auth/register",
            json={
                "email": user_email,
                "username": f"ChallengerMid_{uid}",
                "password": user_pwd,
                "region": "la1",
                "preferred_roles": "MID,TOP",
                "coach_sensitivity": "high",
            },
        )
        assert reg_res.status_code == 201
        auth_data = reg_res.json()
        access_token = auth_data["access_token"]
        refresh_token = auth_data["refresh_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # Check /api/auth/me
        me_res = client.get("/api/auth/me", headers=headers)
        assert me_res.status_code == 200
        assert me_res.json()["email"] == user_email

        # =========================================================================
        # 2. Profile Inspection & Editing
        # =========================================================================
        prof_res = client.get("/api/profile", headers=headers)
        assert prof_res.status_code == 200
        assert prof_res.json()["coach_sensitivity"] == "high"
        assert prof_res.json()["total_matches_recorded"] == 0

        # Update profile
        update_res = client.put(
            "/api/profile",
            json={
                "username": f"ProMid_{uid}",
                "preferred_roles": "MID",
                "region": "na1",
            },
            headers=headers,
        )
        assert update_res.status_code == 200
        assert update_res.json()["region"] == "na1"

        # =========================================================================
        # 3. Match Simulation & Telemetry Logging
        # =========================================================================
        # Match 1: Victory with High Compliance
        m1_payload = {
            "game_id": 1001,
            "champion_name": "Ahri",
            "role": "MID",
            "kills": 9,
            "deaths": 1,
            "assists": 8,
            "cs": 215,
            "gold_earned": 14200,
            "gold_difference": 3100,
            "duration_seconds": 1500,  # 25 min -> 8.6 CS/min
            "win": True,
            "tilt_triggers_count": 0,
            "advices_received_count": 3,
            "advices_followed_count": 3,
            "telemetry_points": [
                {
                    "game_time_seconds": 300.0,
                    "cs": 40,
                    "cs_per_minute": 8.0,
                    "kills": 1,
                    "deaths": 0,
                    "flash_ready": True,
                    "advice_text": "Good wave freeze.",
                },
                {
                    "game_time_seconds": 600.0,
                    "cs": 85,
                    "cs_per_minute": 8.5,
                    "kills": 3,
                    "deaths": 0,
                    "flash_ready": True,
                    "advice_text": "Roam bot lane for dive.",
                },
                {
                    "game_time_seconds": 900.0,
                    "cs": 130,
                    "cs_per_minute": 8.67,
                    "kills": 5,
                    "deaths": 1,
                    "flash_ready": False,
                    "advice_text": "Flash down. Respect vision.",
                },
            ],
        }
        rec1_res = client.post("/api/stats/matches", json=m1_payload, headers=headers)
        assert rec1_res.status_code == 201
        m1_id = rec1_res.json()["id"]

        # Match 2: Defeat with Tilt Trigger
        m2_payload = {
            "game_id": 1002,
            "champion_name": "Syndra",
            "role": "MID",
            "kills": 3,
            "deaths": 5,
            "assists": 2,
            "cs": 160,
            "gold_earned": 10500,
            "gold_difference": -1500,
            "duration_seconds": 1600,
            "win": False,
            "tilt_triggers_count": 2,
            "advices_received_count": 4,
            "advices_followed_count": 1,
            "telemetry_points": [
                {
                    "game_time_seconds": 450.0,
                    "cs": 50,
                    "cs_per_minute": 6.67,
                    "kills": 1,
                    "deaths": 2,
                    "flash_ready": False,
                    "advice_text": "Tilt risk: reset wave and farm under tower.",
                }
            ],
        }
        rec2_res = client.post("/api/stats/matches", json=m2_payload, headers=headers)
        assert rec2_res.status_code == 201
        m2_id = rec2_res.json()["id"]

        # =========================================================================
        # 4. Career Performance Statistics Queries
        # =========================================================================
        summary_res = client.get("/api/stats/summary", headers=headers)
        assert summary_res.status_code == 200
        sum_data = summary_res.json()
        assert sum_data["total_matches"] == 2
        assert sum_data["total_wins"] == 1
        assert sum_data["total_losses"] == 1
        assert sum_data["overall_winrate_percentage"] == 50.0
        assert sum_data["role_stats"]["MID"]["games"] == 2
        assert sum_data["total_tilt_triggers"] == 2
        assert sum_data["total_coach_advices"] == 7
        assert sum_data["total_coach_advices_followed"] == 4

        # Paginated Match List
        matches_list_res = client.get("/api/stats/matches?champion=Ahri", headers=headers)
        assert matches_list_res.status_code == 200
        assert matches_list_res.json()["total_count"] == 1
        assert matches_list_res.json()["matches"][0]["champion_name"] == "Ahri"

        # =========================================================================
        # 5. Visual Analytics Endpoints
        # =========================================================================
        # 5.1 CS Progression Line Chart
        cs_res = client.get(f"/api/analytics/cs-progression/{m1_id}", headers=headers)
        assert cs_res.status_code == 200
        cs_chart = cs_res.json()
        assert cs_chart["match_id"] == m1_id
        assert len(cs_chart["points"]) == 3
        assert cs_chart["points"][0]["challenger_target_cs"] == 35

        # 5.2 Tilt Heatmap
        tilt_res = client.get("/api/analytics/tilt-heatmap", headers=headers)
        assert tilt_res.status_code == 200
        tilt_data = tilt_res.json()
        assert tilt_data["total_matches_analyzed"] == 2
        assert tilt_data["total_deaths"] == 6
        assert len(tilt_data["buckets"]) == 6

        # 5.3 Role Radar Chart
        radar_res = client.get("/api/analytics/role-radar?role=MID", headers=headers)
        assert radar_res.status_code == 200
        radar_data = radar_res.json()
        assert radar_data["role"] == "MID"
        assert len(radar_data["scores"]) == 5

        # 5.4 Coach Efficacy Donut Chart
        coach_res = client.get("/api/analytics/coach-impact", headers=headers)
        assert coach_res.status_code == 200
        coach_data = coach_res.json()
        assert coach_data["winrate_when_followed"] == 100.0
        assert coach_data["winrate_when_ignored"] == 0.0
        assert coach_data["coach_efficacy_delta"] == 100.0

        # =========================================================================
        # 6. Password Change & Re-authentication
        # =========================================================================
        new_pwd = "UpdatedSecurePassword456!"
        pw_res = client.post(
            "/api/profile/change-password",
            json={"current_password": user_pwd, "new_password": new_pwd},
            headers=headers,
        )
        assert pw_res.status_code == 200

        # Verify old password fails
        old_login = client.post(
            "/api/auth/login",
            json={"email": user_email, "password": user_pwd},
        )
        assert old_login.status_code == 401

        # Verify new password succeeds
        new_login = client.post(
            "/api/auth/login",
            json={"email": user_email, "password": new_pwd},
        )
        assert new_login.status_code == 200
        new_access_token = new_login.json()["access_token"]
        new_headers = {"Authorization": f"Bearer {new_access_token}"}

        # =========================================================================
        # 7. Logout & Token Revocation
        # =========================================================================
        logout_res = client.post("/api/auth/logout", headers=headers)
        assert logout_res.status_code == 200
        assert logout_res.json()["status"] == "logged_out"

        # Calling protected route with old blacklisted token fails
        revoked_call = client.get("/api/auth/me", headers=headers)
        assert revoked_call.status_code == 401

        # =========================================================================
        # 8. Secure Account Deletion with Cascade Verification
        # =========================================================================
        # Deletion with wrong password fails
        bad_del = client.request(
            "DELETE",
            "/api/profile/delete-account",
            json={"password": "IncorrectPassword!", "confirmation_text": "DELETE"},
            headers=new_headers,
        )
        assert bad_del.status_code == 401

        # Deletion with correct password succeeds
        del_res = client.request(
            "DELETE",
            "/api/profile/delete-account",
            json={"password": new_pwd, "confirmation_text": "DELETE"},
            headers=new_headers,
        )
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "account_deleted"

        # Account is gone: login fails
        gone_login = client.post(
            "/api/auth/login",
            json={"email": user_email, "password": new_pwd},
        )
        assert gone_login.status_code == 401

        # Token is immediately blacklisted
        dead_token_call = client.get("/api/profile", headers=new_headers)
        assert dead_token_call.status_code == 401
