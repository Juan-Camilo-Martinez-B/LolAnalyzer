"""
Maps a League Client match-history payload onto the match shape the UI already reads.

The client often returns the whole game (ten players, timelines, raw blobs).
Only the linked player's summary fields are kept.
"""

from datetime import datetime, timezone
from typing import Any, Optional

QUEUE_LABELS = {
    400: "Normal Draft",
    420: "Ranked Solo",
    430: "Normal Blind",
    440: "Ranked Flex",
    450: "ARAM",
    490: "Quickplay",
    700: "Clash",
    1700: "Arena",
    1900: "URF",
}

# Data Dragon numeric keys. Unknown ids stay as "Champion <id>" instead of a guess.
CHAMPION_NAMES = {
    1: "Annie",
    2: "Olaf",
    3: "Galio",
    4: "Twisted Fate",
    5: "Xin Zhao",
    7: "LeBlanc",
    8: "Vladimir",
    9: "Fiddlesticks",
    11: "Master Yi",
    12: "Alistar",
    13: "Ryze",
    16: "Soraka",
    17: "Teemo",
    18: "Tristana",
    21: "Miss Fortune",
    22: "Ashe",
    23: "Tryndamere",
    24: "Jax",
    25: "Morgana",
    29: "Twitch",
    31: "Cho'Gath",
    32: "Amumu",
    37: "Sona",
    38: "Kassadin",
    39: "Irelia",
    40: "Janna",
    51: "Caitlyn",
    53: "Blitzcrank",
    54: "Malphite",
    55: "Katarina",
    57: "Maokai",
    58: "Renekton",
    59: "Jarvan IV",
    60: "Elise",
    61: "Orianna",
    62: "Wukong",
    64: "Lee Sin",
    67: "Vayne",
    75: "Nasus",
    77: "Udyr",
    78: "Poppy",
    80: "Pantheon",
    81: "Ezreal",
    82: "Mordekaiser",
    84: "Akali",
    86: "Garen",
    89: "Leona",
    90: "Malzahar",
    91: "Talon",
    92: "Riven",
    96: "Kog'Maw",
    98: "Shen",
    99: "Lux",
    101: "Xerath",
    103: "Ahri",
    104: "Graves",
    105: "Fizz",
    110: "Varus",
    111: "Nautilus",
    112: "Viktor",
    114: "Fiora",
    115: "Ziggs",
    117: "Lulu",
    119: "Draven",
    120: "Hecarim",
    121: "Kha'Zix",
    122: "Darius",
    126: "Jayce",
    131: "Diana",
    133: "Quinn",
    134: "Syndra",
    141: "Kayn",
    142: "Zoe",
    143: "Zyra",
    145: "Kai'Sa",
    150: "Gnar",
    154: "Zac",
    157: "Yasuo",
    161: "Vel'Koz",
    164: "Camille",
    201: "Braum",
    202: "Jhin",
    203: "Kindred",
    222: "Jinx",
    223: "Tahm Kench",
    234: "Viego",
    235: "Senna",
    236: "Lucian",
    238: "Zed",
    240: "Kled",
    245: "Ekko",
    254: "Vi",
    266: "Aatrox",
    267: "Nami",
    268: "Azir",
    350: "Yuumi",
    360: "Samira",
    412: "Thresh",
    420: "Illaoi",
    421: "Rek'Sai",
    432: "Bard",
    497: "Rakan",
    498: "Xayah",
    516: "Ornn",
    517: "Sylas",
    518: "Neeko",
    523: "Aphelios",
    526: "Rell",
    555: "Pyke",
    777: "Yone",
    875: "Sett",
    887: "Gwen",
    888: "Renata Glasc",
}

_LANE_ROLE = {
    ("TOP", "SOLO"): "TOP",
    ("TOP", "NONE"): "TOP",
    ("JUNGLE", "NONE"): "JUNGLE",
    ("JUNGLE", "SOLO"): "JUNGLE",
    ("MIDDLE", "SOLO"): "MID",
    ("MIDDLE", "NONE"): "MID",
    ("MID", "SOLO"): "MID",
    ("BOTTOM", "DUO_CARRY"): "ADC",
    ("BOTTOM", "CARRY"): "ADC",
    ("BOTTOM", "DUO"): "ADC",
    ("BOTTOM", "DUO_SUPPORT"): "SUPPORT",
    ("BOTTOM", "SUPPORT"): "SUPPORT",
    ("UTILITY", "DUO_SUPPORT"): "SUPPORT",
    ("NONE", "DUO_SUPPORT"): "SUPPORT",
}


def unwrap_game(game: dict[str, Any]) -> dict[str, Any]:
    inner = game.get("json")
    if isinstance(inner, dict) and ("participants" in inner or "gameId" in inner):
        return inner
    return game


def extract_games(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    games = payload.get("games")
    if isinstance(games, dict):
        inner = games.get("games")
        if isinstance(inner, list):
            return [item for item in inner if isinstance(item, dict)]
    if isinstance(games, list):
        return [item for item in games if isinstance(item, dict)]
    if "gameId" in payload or "participants" in payload:
        return [payload]
    return []


def game_id_of(game: dict[str, Any]) -> Optional[str]:
    current = unwrap_game(game)
    for key in ("gameId", "id"):
        value = current.get(key)
        if value is not None and str(value).isdigit():
            return str(value)
    return None


def needs_detail(game: dict[str, Any]) -> bool:
    current = unwrap_game(game)
    participants = current.get("participants")
    if not isinstance(participants, list) or not participants:
        return True
    first = participants[0] if isinstance(participants[0], dict) else {}
    stats = first.get("stats") if isinstance(first.get("stats"), dict) else first
    return "kills" not in stats and "win" not in stats


def map_lcu_history(payload: Any, puuid: str, details: Optional[dict[str, dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """Turns a current-summoner history document into UI match rows, newest first."""
    extras = details or {}
    mapped: list[dict[str, Any]] = []
    for game in extract_games(payload):
        current = unwrap_game(game)
        if needs_detail(current):
            game_id = game_id_of(current)
            detail = extras.get(game_id or "")
            if isinstance(detail, dict):
                current = unwrap_game(detail)
        row = map_lcu_game(current, puuid)
        if row is not None:
            mapped.append(row)
    mapped.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return mapped


def map_lcu_game(game: dict[str, Any], puuid: str) -> Optional[dict[str, Any]]:
    """One LCU game -> the same summary fields `/api/riot/matches` already returns."""
    current = unwrap_game(game)
    participant = _local_participant(current, puuid)
    if participant is None:
        return None
    stats = participant.get("stats") if isinstance(participant.get("stats"), dict) else participant
    kills = _int(stats.get("kills"))
    deaths = _int(stats.get("deaths"))
    assists = _int(stats.get("assists"))
    duration = _duration_seconds(current)
    cs = _int(stats.get("totalMinionsKilled")) + _int(stats.get("neutralMinionsKilled"))
    queue_id = _int(current.get("queueId"))
    created = current.get("gameCreation") or current.get("gameCreationDate")
    timestamp = None
    if isinstance(created, (int, float)) and created > 0:
        millis = created if created > 10_000_000_000 else created * 1000
        timestamp = datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()
    game_id = game_id_of(current) or ""
    if not game_id:
        return None
    return {
        "matchId": game_id,
        "queueId": queue_id,
        "gameMode": QUEUE_LABELS.get(queue_id) or current.get("gameMode") or "CLASSIC",
        "isWin": bool(stats.get("win")),
        "championName": _champion_name(participant),
        "role": _role(participant),
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kda": round((kills + assists) / deaths, 2) if deaths else float(kills + assists),
        "cs": cs,
        "csPerMin": round(cs / (duration / 60), 2) if duration else 0,
        "durationSec": duration,
        "timestamp": timestamp,
        "items": [_int(stats.get(f"item{index}")) for index in range(6)],
        "trinketId": _int(stats.get("item6")),
        "goldEarned": _int(stats.get("goldEarned")),
        "visionScore": _int(stats.get("visionScore")),
        "damageDealt": _int(stats.get("totalDamageDealtToChampions")),
    }


def _local_participant(game: dict[str, Any], puuid: str) -> Optional[dict[str, Any]]:
    participants = [item for item in game.get("participants") or [] if isinstance(item, dict)]
    identities = [item for item in game.get("participantIdentities") or [] if isinstance(item, dict)]
    if puuid and identities:
        wanted = None
        for identity in identities:
            player = identity.get("player") if isinstance(identity.get("player"), dict) else {}
            if player.get("puuid") == puuid:
                wanted = identity.get("participantId")
                break
        if wanted is None:
            return None
        for participant in participants:
            if participant.get("participantId") == wanted:
                return participant
        return None
    if len(participants) == 1:
        return participants[0]
    return None


def _champion_name(participant: dict[str, Any]) -> str:
    for key in ("championName", "champion"):
        name = participant.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()
    champion_id = participant.get("championId")
    if isinstance(champion_id, int) and champion_id in CHAMPION_NAMES:
        return CHAMPION_NAMES[champion_id]
    if isinstance(champion_id, int) and champion_id > 0:
        return f"Champion {champion_id}"
    return "Unknown"


def _role(participant: dict[str, Any]) -> str:
    for key in ("teamPosition", "individualPosition", "role"):
        normalized = _normalize_role(participant.get(key))
        if normalized:
            return normalized
    timeline = participant.get("timeline") if isinstance(participant.get("timeline"), dict) else {}
    lane = str(timeline.get("lane") or "").upper()
    role = str(timeline.get("role") or "").upper()
    return _LANE_ROLE.get((lane, role), "UNKNOWN")


def _normalize_role(raw: Any) -> Optional[str]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    token = {"BOTTOM": "ADC", "UTILITY": "SUPPORT", "MIDDLE": "MID"}.get(raw.strip().upper(), raw.strip().upper())
    if token in {"TOP", "JUNGLE", "MID", "ADC", "SUPPORT"}:
        return token
    return None


def _duration_seconds(game: dict[str, Any]) -> int:
    raw = game.get("gameDuration")
    if not isinstance(raw, (int, float)):
        raw = game.get("gameLength")
    if not isinstance(raw, (int, float)) or raw <= 0:
        return 0
    if raw > 20_000:
        return int(raw / 1000)
    return int(raw)


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0
