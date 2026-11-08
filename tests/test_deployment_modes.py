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

    async def fake_read(limit=15):
        from app.services.local_match_history import ClientHistory

        return ClientHistory(
            "client-puuid",
            [{"matchId": "55", "queueId": 420, "gameMode": "Ranked Solo"}],
            {"puuid": "client-puuid", "gameName": "Alt", "tagLine": "LAN"},
        )

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
    assert response.json() == {"clientConnected": True, "synced": 1, "puuid": "client-puuid"}
    assert sent["path"] == "/api/history/import" and sent["token"] == "player-jwt"
    assert sent["payload"]["puuid"] == "client-puuid"
    assert sent["payload"]["profile"]["gameName"] == "Alt"


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


def _link_riot(headers: dict[str, str], client: TestClient, puuid: str) -> None:
    """Links a Riot account straight in the database (the real link calls the Riot API)."""
    from sqlalchemy import update

    from app.db.models import User
    from app.db.session import AsyncSessionLocal

    user_id = client.get("/api/auth/me", headers=headers).json()["id"]

    async def write() -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == user_id).values(riot_puuid=puuid))
            await db.commit()

    asyncio.run(write())


def test_cloud_history_keeps_each_league_profile_apart():
    def game(match_id: str) -> dict:
        return {"matchId": match_id, "queueId": 420, "gameMode": "Ranked Solo", "championName": "Yone", "durationSec": 1700}

    with TestClient(create_app("cloud")) as client:
        headers = _headers(client)
        # An unlinked account takes games from any League account open in the client.
        first = client.post("/api/history/import", headers=headers, json={
            "puuid": "p1", "profile": {"gameName": "Main", "tagLine": "LAN", "summonerLevel": 30}, "matches": [game("g1")],
        })
        assert first.json() == {"stored": 1}
        second = client.post("/api/history/import", headers=headers, json={
            "puuid": "p2", "profile": {"gameName": "Alt", "tagLine": "LAN"}, "matches": [game("g2"), game("g3")],
        })
        assert second.json() == {"stored": 2}

        listed = client.get("/api/riot/profiles", headers=headers).json()
        assert [row["gameName"] for row in listed["profiles"]] == ["Alt", "Main"]
        # Without a link the dashboard follows the last profile opened.
        assert listed["default"] == "p2"
        ids = lambda query: {row["matchId"] for row in client.get(f"/api/riot/matches{query}", headers=headers).json()}
        assert ids("") == {"g2", "g3"}
        assert ids("?profile=p1") == {"g1"}
        assert ids("?profile=all") == {"g1", "g2", "g3"}
        assert client.get("/api/riot/stats?profile=p1", headers=headers).json()["gamesAnalyzed"] == 1

        # A linked Riot ID becomes the default, the other profile stays available.
        _link_riot(headers, client, "p1")
        assert client.get("/api/riot/profiles", headers=headers).json()["default"] == "p1"
        assert ids("") == {"g1"}

        bad = client.post("/api/history/import", json={"matches": [{**game("x"), "matchId": "../x"}]}, headers=headers)
        assert bad.status_code == 422


def test_cloud_lane_plan_answers_the_agent():
    with TestClient(create_app("cloud")) as client:
        headers = _headers(client)
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
