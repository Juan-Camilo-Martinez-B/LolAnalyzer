import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.riot_api import RiotUnavailable


def _auth_headers(client: TestClient) -> dict[str, str]:
    email = f"riot_{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "username": "RiotUser",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "favorite_champion": "Ahri",
            "peak_elo": "oro",
            "first_main": "Jinx",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_connect_requires_configuration_and_links_official_account():
    with TestClient(app) as client:
        headers = _auth_headers(client)
        with patch("app.services.riot_api.settings.RIOT_API_KEY", ""):
            missing = client.post(
                "/api/riot/connect",
                json={"game_name": "Player", "tag_line": "LAN", "region": "la1"},
                headers=headers,
            )
        assert missing.status_code == 503

        account = {"puuid": "puuid-1", "gameName": "Player", "tagLine": "LAN"}
        summoner = {"profileIconId": 29, "summonerLevel": 42}
        with patch("app.api.riot.account_by_riot_id", new=AsyncMock(return_value=account)), \
             patch("app.api.riot.summoner_by_puuid", new=AsyncMock(return_value=summoner)), \
             patch("app.core.config.settings.RIOT_API_KEY", "test-key"):
            # require_api_key reads settings inside riot_api, which these mocks skip.
            linked = client.post(
                "/api/riot/connect",
                json={"game_name": "Player", "tag_line": "LAN", "region": "la1"},
                headers=headers,
            )
        assert linked.status_code == 200
        assert linked.json()["puuid"] == "puuid-1"

        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["riot_linked"] is True


def test_matches_translate_riot_errors_without_leaking_the_key():
    with TestClient(app) as client:
        headers = _auth_headers(client)
        with patch("app.api.riot.account_by_riot_id", new=AsyncMock(return_value={"puuid": "puuid-2", "gameName": "A", "tagLine": "B"})), \
             patch("app.api.riot.summoner_by_puuid", new=AsyncMock(return_value={})):
            client.post(
                "/api/riot/connect",
                json={"game_name": "A", "tag_line": "B", "region": "la1"},
                headers=headers,
            )
        with patch(
            "app.api.riot.recent_match_ids",
            new=AsyncMock(side_effect=RiotUnavailable(429, "Riot API rate limit reached. Try again shortly.")),
        ):
            response = client.get("/api/riot/matches", headers=headers)
        assert response.status_code == 429
        assert "RGAPI" not in response.text
        assert "key" not in response.json()["detail"].lower()
