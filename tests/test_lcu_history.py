"""LCU match history mapped onto the match shape the desktop already renders."""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.lcu_match_map import is_rift_row, map_lcu_game, map_lcu_history, rift_status
from app.services.local_match_history import save_local_matches
from app.services.riot_lcu import RiotLCUService

PUUID = "local-puuid"
BLOB = "Z" * 4000


def _game() -> dict:
    return {
        "gameId": 5123456789,
        "gameCreation": 1710000000000,
        "gameDuration": 1800,
        "queueId": 420,
        "gameMode": "CLASSIC",
        "rawMatch": {"participants": BLOB, "timeline": BLOB},
        "participantIdentities": [
            {"participantId": 1, "player": {"puuid": PUUID, "summonerName": "Me"}},
            {"participantId": 2, "player": {"puuid": "someone-else"}},
        ],
        "participants": [
            {
                "participantId": 1,
                "championId": 157,
                "timeline": {"lane": "MIDDLE", "role": "SOLO"},
                "stats": {
                    "win": True,
                    "kills": 8,
                    "deaths": 2,
                    "assists": 6,
                    "totalMinionsKilled": 170,
                    "neutralMinionsKilled": 10,
                    "goldEarned": 13000,
                    "visionScore": 22,
                    "totalDamageDealtToChampions": 21000,
                    "item0": 3157,
                    "item1": 3020,
                    "item2": 3089,
                    "item3": 0,
                    "item4": 0,
                    "item5": 0,
                    "item6": 3340,
                },
            },
            {
                "participantId": 2,
                "championId": 238,
                "timeline": {"lane": "TOP", "role": "SOLO"},
                "stats": {
                    "win": False,
                    "kills": 1,
                    "deaths": 9,
                    "assists": 0,
                    "totalMinionsKilled": 40,
                    "neutralMinionsKilled": 0,
                    "goldEarned": 4000,
                    "visionScore": 4,
                    "totalDamageDealtToChampions": 3000,
                    "item0": 0,
                    "item1": 0,
                    "item2": 0,
                    "item3": 0,
                    "item4": 0,
                    "item5": 0,
                    "item6": 0,
                },
            },
        ],
    }


def test_lcu_history_maps_to_the_ui_match_and_drops_raw_json():
    payload = {"games": {"games": [_game()]}}
    mapped = map_lcu_history(payload, PUUID)

    assert len(mapped) == 1
    match = mapped[0]
    assert match["matchId"] == "5123456789"
    assert match["championName"] == "Yasuo"
    assert match["role"] == "MID"
    assert match["isWin"] is True
    assert match["kills"] == 8
    assert match["deaths"] == 2
    assert match["assists"] == 6
    assert match["kda"] == 7.0
    assert match["cs"] == 180
    assert match["csPerMin"] == 6.0
    assert match["durationSec"] == 1800
    assert match["gameMode"] == "Ranked Solo"
    assert match["items"] == [3157, 3020, 3089, 0, 0, 0]
    assert match["trinketId"] == 3340
    assert match["goldEarned"] == 13000
    assert match["visionScore"] == 22
    assert match["damageDealt"] == 21000
    encoded = json.dumps(match)
    assert BLOB not in encoded
    assert "rawMatch" not in encoded
    assert "participants" not in encoded
    assert "Zed" not in encoded


def test_thin_list_uses_the_game_detail_payload():
    thin = {"games": {"games": [{"gameId": 5123456789, "rawMatch": BLOB}]}}
    mapped = map_lcu_history(thin, PUUID, {"5123456789": _game()})
    assert mapped[0]["championName"] == "Yasuo"
    assert BLOB not in json.dumps(mapped)
    assert map_lcu_game(_game(), "missing-puuid") is None


def test_only_summoners_rift_games_are_kept():
    aram = {**_game(), "gameId": 2, "queueId": 450, "gameMode": "ARAM", "mapId": 12}
    urf = {**_game(), "gameId": 3, "queueId": 1900, "gameMode": "URF", "mapId": 11}
    mapped = map_lcu_history({"games": {"games": [_game(), aram, urf]}}, PUUID)
    assert [match["matchId"] for match in mapped] == ["5123456789"]


def test_rift_status_waits_for_detail_on_thin_entries():
    assert rift_status({"gameId": 1}) is None
    assert rift_status({"queueId": 420}) is True
    assert rift_status({"mapId": 11, "gameMode": "CLASSIC"}) is True
    assert rift_status({"mapId": 11, "gameMode": "URF"}) is False
    assert rift_status({"queueId": 450, "gameMode": "ARAM"}) is False


def test_rift_row_check_matches_the_stored_shape():
    assert is_rift_row({"queueId": 420, "gameMode": "Ranked Solo"})
    assert is_rift_row({"queueId": 0, "gameMode": "CLASSIC"})
    assert not is_rift_row({"queueId": 450, "gameMode": "ARAM"})


def test_unknown_lane_stays_unknown_instead_of_mid():
    game = _game()
    me = game["participants"][0]
    me["timeline"] = {"lane": "NONE", "role": "NONE"}
    assert map_lcu_game(game, PUUID)["role"] == "UNKNOWN"
    me["spell1Id"] = 11
    assert map_lcu_game(game, PUUID)["role"] == "JUNGLE"


def test_game_detail_rejects_a_non_numeric_id():
    service = RiotLCUService()
    assert asyncio.run(service.get_match_history_game("../lockfile")) is None
    assert asyncio.run(service.get_match_history_game("not-a-game")) is None


def _headers(client: TestClient) -> dict[str, str]:
    email = f"hist_{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "username": "HistUser",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "favorite_champion": "Ahri",
            "peak_elo": "oro",
            "first_main": "Jinx",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_closed_client_returns_stored_history_without_riot(monkeypatch):
    stored = map_lcu_history({"games": {"games": [_game()]}}, PUUID)

    async def remember_then_close(db, user, limit=15):
        await save_local_matches(db, user.id, stored, source="lcu")
        return None

    monkeypatch.setattr("app.api.riot.sync_lcu_history", remember_then_close)

    with TestClient(app) as client:
        headers = _headers(client)
        with patch(
            "app.api.riot.account_by_riot_id",
            new=AsyncMock(return_value={"puuid": PUUID, "gameName": "Me", "tagLine": "LAN"}),
        ), patch("app.api.riot.summoner_by_puuid", new=AsyncMock(return_value={})):
            linked = client.post(
                "/api/riot/connect",
                json={"game_name": "Me", "tag_line": "LAN", "region": "la1"},
                headers=headers,
            )
        assert linked.status_code == 200

        def riot_must_not_run(*_args, **_kwargs):
            raise AssertionError("Riot match API should not run when local history exists")

        with patch("app.api.riot.recent_match_ids", new=AsyncMock(side_effect=riot_must_not_run)):
            response = client.get("/api/riot/matches?count=10", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body[0]["matchId"] == "5123456789"
    assert body[0]["championName"] == "Yasuo"
    assert body[0]["kda"] == 7.0
    assert body[0]["csPerMin"] == 6.0
    assert BLOB not in response.text
