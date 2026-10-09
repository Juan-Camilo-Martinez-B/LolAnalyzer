from app.schemas.game_events import GameEventType, Role
from app.services.live_game import parse_live_game


def test_parse_live_game_reads_the_local_player():
    payload = {
        "activePlayer": {"summonerName": "XTHZC7#LAN", "level": 9, "currentGold": 1450},
        "allPlayers": [
            {
                "summonerName": "Enemy#LAN",
                "championName": "Zed",
                "position": "MIDDLE",
                "scores": {"kills": 3, "deaths": 0, "assists": 1, "creepScore": 90},
            },
            {
                "riotId": "XTHZC7#LAN",
                "riotIdGameName": "XTHZC7",
                "riotIdTagLine": "LAN",
                "championName": "Ahri",
                "position": "MIDDLE",
                "scores": {"kills": 2, "deaths": 1, "assists": 4, "creepScore": 78},
                "summonerSpells": {
                    "summonerSpellOne": {"displayName": "Flash"},
                    "summonerSpellTwo": {"displayName": "Ignite"},
                },
            },
        ],
        "gameData": {"gameTime": 612.4},
        "events": {
            "Events": [
                {"EventID": 3, "EventName": "ChampionKill", "EventTime": 180, "KillerName": "Zed", "VictimName": "XTHZC7#LAN", "Assisters": []},
                {"EventID": 4, "EventName": "ChampionKill", "EventTime": 240, "KillerName": "XTHZC7#LAN", "VictimName": "Zed", "Assisters": []},
            ]
        },
    }

    parsed = parse_live_game(payload)
    assert parsed is not None
    telemetry, events, aliases = parsed
    assert telemetry.champion_name == "Ahri"
    assert telemetry.role == Role.MID
    assert telemetry.kills == 2
    assert telemetry.deaths == 1
    assert telemetry.cs == 78
    assert telemetry.current_gold == 1450
    assert "XTHZC7#LAN" in aliases
    assert [event.event_type for _event_id, event in events] == [GameEventType.DEATH, GameEventType.KILL]
