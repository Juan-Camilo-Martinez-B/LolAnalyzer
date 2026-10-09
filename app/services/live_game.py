"""
Reads the local Live Client Data API while a match is in progress and turns
that snapshot into the telemetry the coach already understands.
"""

from typing import Any

import httpx

from app.schemas.game_events import (
    GameEvent,
    GameEventType,
    PlayerTelemetry,
    Role,
    SummonerSpellState,
    SummonerSpellType,
)

LIVE_CLIENT_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"

ROLE_BY_POSITION = {
    "TOP": Role.TOP,
    "JUNGLE": Role.JUNGLE,
    "MIDDLE": Role.MID,
    "MID": Role.MID,
    "BOTTOM": Role.ADC,
    "ADC": Role.ADC,
    "UTILITY": Role.SUPPORT,
    "SUPPORT": Role.SUPPORT,
}

SPELL_BY_NAME = {
    "FLASH": SummonerSpellType.FLASH,
    "DESTELLO": SummonerSpellType.FLASH,
    "TELEPORT": SummonerSpellType.TELEPORT,
    "IGNITE": SummonerSpellType.IGNITE,
    "HEAL": SummonerSpellType.HEAL,
    "GHOST": SummonerSpellType.GHOST,
    "BARRIER": SummonerSpellType.BARRIER,
    "EXHAUST": SummonerSpellType.EXHAUST,
    "SMITE": SummonerSpellType.SMITE,
    "CLEANSE": SummonerSpellType.CLEANSE,
}


def _aliases(player: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("summonerName", "riotId", "riotIdGameName"):
        value = player.get(key)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    game_name = player.get("riotIdGameName")
    tag_line = player.get("riotIdTagLine")
    if isinstance(game_name, str) and isinstance(tag_line, str) and game_name and tag_line:
        names.add(f"{game_name}#{tag_line}")
    return names


def _local_player(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    active = payload.get("activePlayer")
    players = payload.get("allPlayers")
    if not isinstance(active, dict) or not isinstance(players, list):
        return None

    active_names = _aliases(active)
    for player in players:
        if isinstance(player, dict) and _aliases(player) & active_names:
            return active, player
    return None


def _spell(raw: Any, slot: str) -> SummonerSpellState | None:
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("displayName") or raw.get("rawDisplayName") or "").upper()
    spell_name = SPELL_BY_NAME.get(label, SummonerSpellType.OTHER)
    return SummonerSpellState(spell_name=spell_name, slot=slot, is_ready=True)


def parse_live_game(payload: dict[str, Any]) -> tuple[PlayerTelemetry, list[tuple[str, GameEvent]], set[str]] | None:
    """Maps one Live Client payload into telemetry, personal events, and name aliases."""
    matched = _local_player(payload)
    if matched is None:
        return None

    active, player = matched
    aliases = _aliases(active) | _aliases(player)
    scores = player.get("scores") if isinstance(player.get("scores"), dict) else {}
    game_data = payload.get("gameData") if isinstance(payload.get("gameData"), dict) else {}
    position = str(player.get("position") or "").upper()
    spells_raw = player.get("summonerSpells") if isinstance(player.get("summonerSpells"), dict) else {}
    spells = [
        spell
        for spell in (
            _spell(spells_raw.get("summonerSpellOne"), "D"),
            _spell(spells_raw.get("summonerSpellTwo"), "F"),
        )
        if spell is not None
    ]

    telemetry = PlayerTelemetry(
        summoner_name=next(iter(aliases), "Summoner"),
        champion_name=str(player.get("championName") or "Unknown"),
        role=ROLE_BY_POSITION.get(position, Role.UNKNOWN),
        level=int(active.get("level") or player.get("level") or 1),
        current_gold=int(float(active.get("currentGold") or 0)),
        kills=int(scores.get("kills") or 0),
        deaths=int(scores.get("deaths") or 0),
        assists=int(scores.get("assists") or 0),
        cs=int(scores.get("creepScore") or 0),
        game_time_seconds=float(game_data.get("gameTime") or 0),
        summoner_spells=spells,
    )

    events: list[tuple[str, GameEvent]] = []
    event_block = payload.get("events") if isinstance(payload.get("events"), dict) else {}
    for raw in event_block.get("Events") or []:
        if not isinstance(raw, dict) or raw.get("EventName") != "ChampionKill":
            continue
        victim = str(raw.get("VictimName") or "")
        killer = str(raw.get("KillerName") or "")
        assisters = [str(name) for name in (raw.get("Assisters") or [])]
        if victim in aliases:
            event_type = GameEventType.DEATH
        elif killer in aliases:
            event_type = GameEventType.KILL
        elif any(name in aliases for name in assisters):
            event_type = GameEventType.ASSIST
        else:
            continue
        event_id = str(raw.get("EventID"))
        events.append((
            event_id,
            GameEvent(
                event_id=event_id,
                event_type=event_type,
                game_time_seconds=float(raw.get("EventTime") or 0),
                raw_data={"victim": victim, "killer": killer},
            ),
        ))

    return telemetry, events, aliases


async def fetch_live_game() -> dict[str, Any] | None:
    """Returns the current match snapshot, or None when the game client is closed."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=2.0) as client:
            response = await client.get(LIVE_CLIENT_URL)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    body = response.json()
    return body if isinstance(body, dict) else None
