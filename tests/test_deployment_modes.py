"""Cloud (Vercel) and local (desktop agent) builds expose only their own routes."""

import asyncio
import uuid

from fastapi.testclient import TestClient

from app.main import create_app  # noqa: I001 - the app must load before the models (import cycle)
from app.core.config import settings
from app.services import matchup_plan as matchup_module


def paths(app) -> set[str]:
    return {route.path for route in app.routes}


def test_cloud_build_has_accounts_and_no_league_client_routes():
    cloud = paths(create_app("cloud"))
    assert {"/api/auth/login", "/api/history/import", "/api/ai/matchup-plan", "/api/analysis/latest"} <= cloud
    assert "/api/coach/metrics" in cloud and "/api/coach/advice" in cloud
    assert not any(path.startswith("/api/champ-select") for path in cloud)
    assert "/api/coach/live" not in cloud and "/ws/events" not in cloud


def test_local_build_has_only_league_client_routes():
    local = paths(create_app("local"))
    assert {"/api/champ-select/loadout", "/api/coach/live", "/api/local/history/sync", "/api/local/profile"} <= local
    assert not any(path.startswith("/api/auth") for path in local)
    assert "/api/history/import" not in local and "/api/coach/metrics" not in local


def test_cloud_health_does_not_touch_the_league_client():
    with TestClient(create_app("cloud")) as client:
        body = client.get("/health").json()
    assert body["mode"] == "cloud" and "lcu_connected" not in body


def test_local_agent_requires_its_launch_token(monkeypatch):
    monkeypatch.setattr(settings, "APP_MODE", "local")
    monkeypatch.setattr(settings, "AGENT_TOKEN", "launch-token")

    async def no_client():
        return False

    monkeypatch.setattr("app.api.champ_select.client_connected", no_client)
    with TestClient(create_app("local")) as client:
        assert client.get("/api/champ-select/loadout?champion_id=777").status_code == 401
        wrong = client.get("/api/champ-select/loadout?champion_id=777", headers={"X-Agent-Token": "guess"})
        assert wrong.status_code == 401
        ok = client.get("/api/champ-select/loadout?champion_id=777", headers={"X-Agent-Token": "launch-token"})
        assert ok.status_code == 200 and ok.json()["clientConnected"] is False


def test_local_agent_without_a_configured_token_refuses_everyone(monkeypatch):
    monkeypatch.setattr(settings, "APP_MODE", "local")
    monkeypatch.setattr(settings, "AGENT_TOKEN", "")
    with TestClient(create_app("local")) as client:
        assert client.get("/api/local/profile", headers={"X-Agent-Token": ""}).status_code == 401


def test_local_history_sync_forwards_to_the_cloud_with_the_player_token(monkeypatch):
    monkeypatch.setattr(settings, "APP_MODE", "local")
    monkeypatch.setattr(settings, "AGENT_TOKEN", "launch-token")
    sent = {}

    async def fake_read(limit=15, linked_puuid=None):
        return "client-puuid", [{"matchId": "55", "queueId": 420, "gameMode": "Ranked Solo"}]

    async def fake_post(path, token, payload):
        sent.update(path=path, token=token, payload=payload)
        return {"stored": 1}

    monkeypatch.setattr("app.api.local_bridge.read_lcu_history", fake_read)
    monkeypatch.setattr("app.api.local_bridge.cloud_post", fake_post)
    with TestClient(create_app("local")) as client:
        response = client.post(
            "/api/local/history/sync",
            headers={"X-Agent-Token": "launch-token", "Authorization": "Bearer player-jwt"},
        )
    assert response.json() == {"clientConnected": True, "synced": 1}
    assert sent["path"] == "/api/history/import" and sent["token"] == "player-jwt"
    assert sent["payload"]["puuid"] == "client-puuid"


def test_local_lane_plan_comes_from_the_cloud(monkeypatch):
    monkeypatch.setattr(settings, "APP_MODE", "local")

    async def fake_post(path, token, payload):
        assert path == "/api/ai/matchup-plan" and token == "jwt" and payload["opponent"] == "Ahri"
        return {"lane": "Juega seguro.", "fight": "Tras su E.", "avoid": "Su encanto.", "source": "groq"}

    monkeypatch.setattr(matchup_module, "cloud_post", fake_post)
    plan = asyncio.run(matchup_module.matchup_plan_for("Yone", "Ahri", "mid", {"tip": "x"}, "jwt"))
    assert plan == {"lane": "Juega seguro.", "fight": "Tras su E.", "avoid": "Su encanto.", "source": "groq"}


def _headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"cloud_{uuid.uuid4().hex[:8]}@example.com",
            "username": "CloudUser",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "favorite_champion": "Ahri",
            "peak_elo": "oro",
            "first_main": "Jinx",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_cloud_history_import_stores_games_and_refuses_another_account():
    match = {"matchId": "g1", "queueId": 420, "gameMode": "Ranked Solo", "championName": "Yone", "durationSec": 1700}
    with TestClient(create_app("cloud")) as client:
        headers = _headers(client)
        stored = client.post("/api/history/import", json={"puuid": "p1", "matches": [match]}, headers=headers)
        assert stored.json() == {"stored": 1}
        bad = client.post("/api/history/import", json={"matches": [{**match, "matchId": "../x"}]}, headers=headers)
        assert bad.status_code == 422
        plan = client.post(
            "/api/ai/matchup-plan",
            json={"champion": "Yone", "opponent": "Ahri", "lane": "mid", "facts": {"laneAdvantage": "Ahri"}},
            headers=headers,
        )
        assert plan.status_code == 200 and "Ahri" in plan.json()["lane"]


def test_local_agent_guards_read_only_client_routes(monkeypatch):
    monkeypatch.setattr(settings, "APP_MODE", "local")
    monkeypatch.setattr(settings, "AGENT_TOKEN", "launch-token")

    async def closed_client():
        return None

    monkeypatch.setattr("app.api.champ_select.riot_lcu_service.get_gameflow_phase", closed_client)
    with TestClient(create_app("local")) as client:
        for path in ("status", "session", "summoner", "gameflow"):
            assert client.get(f"/api/champ-select/{path}").status_code == 401
        ok = client.get("/api/champ-select/gameflow", headers={"X-Agent-Token": "launch-token"})
        assert ok.status_code == 200
        assert ok.json() == {"connected": False, "phase": "None", "in_champ_select": False, "in_game": False}
