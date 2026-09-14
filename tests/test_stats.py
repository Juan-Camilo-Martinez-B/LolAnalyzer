"""
Integration and unit tests for Match History, Telemetry Logging, Aggregated Stats Summary,
and Symmetrical Match Reset operations.
"""

import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _get_authenticated_client(client: TestClient):
    uid = uuid.uuid4().hex[:8]
    email = f"stats_user_{uid}@riot.gg"
    reg_res = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "username": f"StatsPlayer_{uid}",
            "password": "Password123!",
            "region": "la1",
            "preferred_roles": "MID,TOP",
        },
    )
    token = reg_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers, email


class TestStatsAPI:
    def test_record_match_with_telemetry_and_detail(self, client: TestClient):
        _, headers, _ = _get_authenticated_client(client)

        match_payload = {
            "game_id": 12345678,
            "champion_name": "Ahri",
            "role": "MID",
            "kills": 8,
            "deaths": 2,
            "assists": 5,
            "cs": 180,
            "gold_earned": 12500,
            "gold_difference": 2100,
            "duration_seconds": 1500,  # 25 minutes
            "win": True,
            "tilt_triggers_count": 1,
            "advices_received_count": 3,
            "advices_followed_count": 2,
            "telemetry_points": [
                {
                    "game_time_seconds": 300.0,
                    "cs": 35,
                    "cs_per_minute": 7.0,
                    "kills": 1,
                    "deaths": 0,
                    "flash_ready": True,
                    "advice_text": "Good wave control.",
                },
                {
                    "game_time_seconds": 600.0,
                    "cs": 80,
                    "cs_per_minute": 8.0,
                    "kills": 3,
                    "deaths": 1,
                    "flash_ready": False,
                    "advice_text": "Flash down. Play safe.",
                },
            ],
        }

        # 1. Record match
        res = client.post("/api/stats/matches", json=match_payload, headers=headers)
        assert res.status_code == 201
        data = res.json()
        match_id = data["id"]
        assert data["champion_name"] == "Ahri"
        assert data["role"] == "MID"
        assert data["kda_ratio"] == 6.5  # (8+5)/2
        assert data["cs_per_minute"] == 7.2  # 180 / 25
        assert len(data["telemetry_points"]) == 2
        assert data["telemetry_points"][0]["advice_text"] == "Good wave control."

        # 2. Get match detail by ID
        detail_res = client.get(f"/api/stats/matches/{match_id}", headers=headers)
        assert detail_res.status_code == 200
        detail_data = detail_res.json()
        assert detail_data["id"] == match_id
        assert len(detail_data["telemetry_points"]) == 2

        # 3. 404 on nonexistent match ID
        not_found_res = client.get("/api/stats/matches/999999", headers=headers)
        assert not_found_res.status_code == 404

    def test_filtered_matches_and_summary_and_reset(self, client: TestClient):
        _, headers, _ = _get_authenticated_client(client)

        # 1. Record 3 diverse matches
        matches = [
            {
                "champion_name": "Ahri",
                "role": "MID",
                "kills": 10,
                "deaths": 2,
                "assists": 8,
                "cs": 200,
                "gold_earned": 14000,
                "gold_difference": 3000,
                "duration_seconds": 1800,  # 30 min -> 6.67 cs/min
                "win": True,
                "tilt_triggers_count": 0,
                "advices_received_count": 4,
                "advices_followed_count": 4,
            },
            {
                "champion_name": "Zed",
                "role": "MID",
                "kills": 6,
                "deaths": 6,
                "assists": 2,
                "cs": 160,
                "gold_earned": 11000,
                "gold_difference": -500,
                "duration_seconds": 1500,
                "win": False,
                "tilt_triggers_count": 2,
                "advices_received_count": 3,
                "advices_followed_count": 1,
            },
            {
                "champion_name": "Darius",
                "role": "TOP",
                "kills": 4,
                "deaths": 1,
                "assists": 5,
                "cs": 190,
                "gold_earned": 13000,
                "gold_difference": 1500,
                "duration_seconds": 1600,
                "win": True,
                "tilt_triggers_count": 0,
                "advices_received_count": 2,
                "advices_followed_count": 2,
            },
        ]

        created_ids = []
        for m in matches:
            r = client.post("/api/stats/matches", json=m, headers=headers)
            assert r.status_code == 201
            created_ids.append(r.json()["id"])

        # 2. List all matches
        all_res = client.get("/api/stats/matches", headers=headers)
        assert all_res.status_code == 200
        assert all_res.json()["total_count"] == 3
        assert len(all_res.json()["matches"]) == 3

        # 3. Filter by role=MID
        mid_res = client.get("/api/stats/matches?role=MID", headers=headers)
        assert mid_res.status_code == 200
        assert mid_res.json()["total_count"] == 2

        # 4. Filter by champion=Ahri
        ahri_res = client.get("/api/stats/matches?champion=Ahri", headers=headers)
        assert ahri_res.status_code == 200
        assert ahri_res.json()["total_count"] == 1
        assert ahri_res.json()["matches"][0]["champion_name"] == "Ahri"

        # 5. Filter by win=false
        loss_res = client.get("/api/stats/matches?win=false", headers=headers)
        assert loss_res.status_code == 200
        assert loss_res.json()["total_count"] == 1
        assert loss_res.json()["matches"][0]["champion_name"] == "Zed"

        # 6. Pagination check (limit=1, skip=1)
        page_res = client.get("/api/stats/matches?limit=1&skip=1", headers=headers)
        assert page_res.status_code == 200
        assert len(page_res.json()["matches"]) == 1

        # 7. Check Career Summary stats
        summary_res = client.get("/api/stats/summary", headers=headers)
        assert summary_res.status_code == 200
        s_data = summary_res.json()
        assert s_data["total_matches"] == 3
        assert s_data["total_wins"] == 2
        assert s_data["total_losses"] == 1
        assert s_data["overall_winrate_percentage"] == 66.7
        assert "MID" in s_data["role_stats"]
        assert s_data["role_stats"]["MID"]["games"] == 2
        assert s_data["role_stats"]["MID"]["wins"] == 1
        assert s_data["role_stats"]["TOP"]["games"] == 1
        assert s_data["role_stats"]["TOP"]["wins"] == 1
        assert len(s_data["top_champions"]) == 3
        assert s_data["total_tilt_triggers"] == 2
        assert s_data["total_coach_advices"] == 9
        assert s_data["total_coach_advices_followed"] == 7
        assert s_data["coach_compliance_rate_percentage"] == 77.8

        # 8. Delete a single match
        first_id = created_ids[0]
        del_one = client.delete(f"/api/stats/matches/{first_id}", headers=headers)
        assert del_one.status_code == 200
        assert del_one.json()["status"] == "deleted"

        # Check total is now 2
        after_del = client.get("/api/stats/matches", headers=headers)
        assert after_del.json()["total_count"] == 2

        # 9. Reset all user stats
        reset_res = client.delete("/api/stats/reset", headers=headers)
        assert reset_res.status_code == 200
        assert reset_res.json()["status"] == "stats_reset"
        assert reset_res.json()["deleted_matches_count"] == 2

        # 10. Check empty summary after reset
        empty_sum = client.get("/api/stats/summary", headers=headers)
        assert empty_sum.status_code == 200
        assert empty_sum.json()["total_matches"] == 0
        assert empty_sum.json()["overall_winrate_percentage"] == 0.0
