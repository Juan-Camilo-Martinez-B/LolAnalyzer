"""
Integration and unit tests for Visual Analytics and Chart Data APIs
(CS Progression Line Chart, Tilt Heatmap, 5-Axis Tactical Radar, Coach Impact Donut Chart).
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
    email = f"analytics_user_{uid}@riot.gg"
    reg_res = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "username": f"ChartPlayer_{uid}",
            "password": "Password123!",
            "region": "la1",
            "preferred_roles": "MID",
        },
    )
    token = reg_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers, email


class TestVisualAnalyticsAPI:
    def test_cs_progression_endpoint(self, client: TestClient):
        _, headers, _ = _get_authenticated_client(client)

        # 1. Create a match with rich telemetry time-series
        match_payload = {
            "game_id": 98765432,
            "champion_name": "Orianna",
            "role": "MID",
            "kills": 5,
            "deaths": 1,
            "assists": 7,
            "cs": 195,
            "gold_earned": 13000,
            "gold_difference": 1800,
            "duration_seconds": 1500,  # 25 min
            "win": True,
            "tilt_triggers_count": 0,
            "advices_received_count": 2,
            "advices_followed_count": 2,
            "telemetry_points": [
                {
                    "game_time_seconds": 300.0,  # 5 min
                    "cs": 42,
                    "cs_per_minute": 8.4,
                    "kills": 0,
                    "deaths": 0,
                    "flash_ready": True,
                    "advice_text": "Good wave control.",
                },
                {
                    "game_time_seconds": 600.0,  # 10 min
                    "cs": 88,
                    "cs_per_minute": 8.8,
                    "kills": 2,
                    "deaths": 0,
                    "flash_ready": True,
                    "advice_text": None,
                },
                {
                    "game_time_seconds": 900.0,  # 15 min
                    "cs": 125,
                    "cs_per_minute": 8.33,
                    "kills": 3,
                    "deaths": 1,
                    "flash_ready": False,
                    "advice_text": "Flash on cooldown. Respect fog of war.",
                },
            ],
        }

        rec_res = client.post("/api/stats/matches", json=match_payload, headers=headers)
        assert rec_res.status_code == 201
        match_id = rec_res.json()["id"]

        # 2. Query CS Progression chart endpoint
        chart_res = client.get(f"/api/analytics/cs-progression/{match_id}", headers=headers)
        assert chart_res.status_code == 200
        data = chart_res.json()

        assert data["match_id"] == match_id
        assert data["champion_name"] == "Orianna"
        assert data["role"] == "MID"
        assert data["duration_minutes"] == 25.0
        assert data["total_cs"] == 195
        assert len(data["points"]) == 3

        p0 = data["points"][0]
        assert p0["timestamp_minute"] == 5.0
        assert p0["actual_cs"] == 42
        assert p0["actual_cs_per_min"] == 8.4
        assert p0["challenger_target_cs"] == 35  # 10 * (5 - 1.5) = 35
        assert p0["has_cs_crash"] is False
        assert p0["advice_text"] == "Good wave control."

        # 3. 404 on nonexistent match ID
        not_found = client.get("/api/analytics/cs-progression/999999", headers=headers)
        assert not_found.status_code == 404

    def test_tilt_heatmap_and_role_radar_and_coach_impact(self, client: TestClient):
        _, headers, _ = _get_authenticated_client(client)

        # 1. Test empty radar state initially
        empty_radar = client.get("/api/analytics/role-radar", headers=headers)
        assert empty_radar.status_code == 200
        assert empty_radar.json()["matches_evaluated"] == 0
        assert empty_radar.json()["overall_performance_rating"] == "N/A"
        assert len(empty_radar.json()["scores"]) == 5

        # 2. Record 2 matches with distinct events
        # Match 1: High compliance & win
        m1 = {
            "champion_name": "Ahri",
            "role": "MID",
            "kills": 8,
            "deaths": 1,
            "assists": 6,
            "cs": 210,
            "gold_earned": 14500,
            "gold_difference": 3200,
            "duration_seconds": 1600,
            "win": True,
            "tilt_triggers_count": 0,
            "advices_received_count": 3,
            "advices_followed_count": 3,
            "telemetry_points": [
                {
                    "game_time_seconds": 400.0,  # 5-10m
                    "cs": 55,
                    "cs_per_minute": 8.25,
                    "kills": 2,
                    "deaths": 1,
                    "flash_ready": True,
                    "advice_text": "Good trade.",
                }
            ],
        }
        # Match 2: Low compliance, tilt trigger & loss
        m2 = {
            "champion_name": "Yasuo",
            "role": "MID",
            "kills": 3,
            "deaths": 7,
            "assists": 2,
            "cs": 140,
            "gold_earned": 9500,
            "gold_difference": -2000,
            "duration_seconds": 1500,
            "win": False,
            "tilt_triggers_count": 2,
            "advices_received_count": 4,
            "advices_followed_count": 1,
            "telemetry_points": [
                {
                    "game_time_seconds": 700.0,  # 10-15m
                    "cs": 70,
                    "cs_per_minute": 6.0,
                    "kills": 1,
                    "deaths": 3,
                    "flash_ready": False,
                    "advice_text": "Tilt risk detected: breathe and reset wave.",
                }
            ],
        }

        r1 = client.post("/api/stats/matches", json=m1, headers=headers)
        r2 = client.post("/api/stats/matches", json=m2, headers=headers)
        assert r1.status_code == 201
        assert r2.status_code == 201

        # 3. Test Tilt Heatmap endpoint
        heatmap_res = client.get("/api/analytics/tilt-heatmap", headers=headers)
        assert heatmap_res.status_code == 200
        h_data = heatmap_res.json()
        assert h_data["total_matches_analyzed"] == 2
        assert h_data["total_deaths"] == 8
        assert h_data["total_tilt_triggers"] == 2
        assert len(h_data["buckets"]) == 6

        # 4. Test Role Radar endpoint
        radar_res = client.get("/api/analytics/role-radar?role=MID", headers=headers)
        assert radar_res.status_code == 200
        r_data = radar_res.json()
        assert r_data["role"] == "MID"
        assert r_data["matches_evaluated"] == 2
        assert r_data["overall_performance_rating"] in ["S+", "S", "A", "B", "C"]
        assert len(r_data["scores"]) == 5
        axes = [s["axis"] for s in r_data["scores"]]
        assert "Farming" in axes
        assert "Combat" in axes
        assert "Vision & Safety" in axes
        assert "Objectives & Gold" in axes
        assert "Survivability" in axes

        # 5. Test Coach Impact endpoint
        impact_res = client.get("/api/analytics/coach-impact", headers=headers)
        assert impact_res.status_code == 200
        i_data = impact_res.json()
        assert i_data["total_advices_received"] == 7
        assert i_data["total_advices_followed"] == 4
        assert i_data["compliance_rate_percentage"] == 57.1
        assert i_data["winrate_when_followed"] == 100.0
        assert i_data["winrate_when_ignored"] == 0.0
        assert i_data["coach_efficacy_delta"] == 100.0
        assert len(i_data["slices"]) == 4
