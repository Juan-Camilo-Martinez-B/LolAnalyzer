import json

from app.services.live_game import parse_live_game
from scripts.fixture_anonymizer import anonymize_payload


def _live_payload():
    return {
        "activePlayer": {
            "summonerName": "RealPlayer#LAN",
            "riotId": "RealPlayer#LAN",
            "riotIdGameName": "RealPlayer",
            "riotIdTagLine": "LAN",
            "level": 7,
            "currentGold": 900,
        },
        "allPlayers": [
            {
                "riotId": "RealPlayer#LAN",
                "riotIdGameName": "RealPlayer",
                "riotIdTagLine": "LAN",
                "summonerName": "RealPlayer#LAN",
                "championName": "Ahri",
                "position": "MIDDLE",
                "scores": {"kills": 1, "deaths": 2, "assists": 0, "creepScore": 55},
            },
            {
                "riotId": "Rival#EUW",
                "riotIdGameName": "Rival",
                "riotIdTagLine": "EUW",
                "summonerName": "Rival#EUW",
                "championName": "Zed",
                "position": "MIDDLE",
                "scores": {"kills": 2, "deaths": 1, "assists": 0, "creepScore": 60},
            },
        ],
        "gameData": {"gameTime": 400.0},
        "events": {
            "Events": [
                {"EventID": 1, "EventName": "ChampionKill", "EventTime": 200, "KillerName": "Rival#EUW", "VictimName": "RealPlayer#LAN", "Assisters": []},
                {"EventID": 2, "EventName": "ChampionKill", "EventTime": 300, "KillerName": "RealPlayer#LAN", "VictimName": "Rival#EUW", "Assisters": ["Rival#EUW"]},
            ]
        },
    }


def test_no_real_identity_survives():
    text = json.dumps(anonymize_payload(_live_payload()))
    for secret in ("RealPlayer", "Rival", "LAN", "EUW"):
        assert secret not in text


def test_aliases_are_consistent_between_players_and_events():
    result = anonymize_payload(_live_payload())
    local_alias = result["activePlayer"]["riotId"]
    assert result["allPlayers"][0]["riotId"] == local_alias
    assert result["events"]["Events"][0]["VictimName"] == local_alias
    assert result["events"]["Events"][1]["KillerName"] == local_alias
    assert result["allPlayers"][1]["riotIdGameName"] == "Player2"


def test_anonymized_payload_still_parses_as_live_game():
    parsed = parse_live_game(anonymize_payload(_live_payload()))
    assert parsed is not None
    telemetry, _events, _aliases = parsed
    assert telemetry.champion_name == "Ahri"
    assert telemetry.deaths == 2


def test_champ_select_ids_are_replaced_and_empty_slots_kept():
    session = {
        "localPlayerCellId": 0,
        "myTeam": [
            {"cellId": 0, "gameName": "RealPlayer", "tagLine": "LAN", "puuid": "abc-123", "summonerId": 98765},
            {"cellId": 1, "gameName": "", "tagLine": "", "puuid": "", "summonerId": 0},
        ],
    }
    result = anonymize_payload(session)
    me, bot = result["myTeam"]
    assert me["gameName"] == "Player1"
    assert me["tagLine"] == "ANON"
    assert me["puuid"].startswith("anon-puuid-")
    assert me["summonerId"] == 1
    assert bot == session["myTeam"][1]
    assert session["myTeam"][0]["gameName"] == "RealPlayer"
