"""
Reads match history from the local League client and stores the UI summary.

The Riot API is not required for this path. A later request can serve the
stored rows after the client closes or the development key expires.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, NamedTuple, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LocalMatchHistory, RiotProfile, utc_now
from app.services.lcu_match_map import (
    SUMMONERS_RIFT_QUEUES,
    extract_games,
    game_id_of,
    is_rift_row,
    map_lcu_history,
    needs_detail,
    rift_status,
    unwrap_game,
)
from app.services.riot_lcu import riot_lcu_service

logger = logging.getLogger("lol_analyzer.local_history")

# Selector value for every League profile of the account at once.
ALL_PROFILES = "all"


class ClientHistory(NamedTuple):
    """Games read from the open client and the League account they belong to."""

    puuid: str
    matches: list[dict[str, Any]]
    profile: dict[str, Any]


def solo_from_lcu(ranked_stats: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """LCU ranked payload -> the league-v4 shape the desktop already reads."""
    queues = (ranked_stats or {}).get("queues") or []
    entry = next((q for q in queues if isinstance(q, dict) and q.get("queueType") == "RANKED_SOLO_5x5"), None)
    if not entry or not entry.get("tier"):
        return None
    return {
        "tier": entry.get("tier"),
        "rank": entry.get("division") or "I",
        "leaguePoints": int(entry.get("leaguePoints") or 0),
        "wins": int(entry.get("wins") or 0),
        "losses": int(entry.get("losses") or 0),
        "queueType": "RANKED_SOLO_5x5",
    }


def _played_at(match: dict[str, Any]) -> datetime:
    raw = match.get("timestamp")
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
    return datetime.now(timezone.utc)


def _apply(row: LocalMatchHistory, match: dict[str, Any], source: str) -> None:
    row.champion_name = str(match.get("championName") or "Unknown")[:100]
    row.role = str(match.get("role") or "UNKNOWN")[:20]
    row.kills = int(match.get("kills") or 0)
    row.deaths = int(match.get("deaths") or 0)
    row.assists = int(match.get("assists") or 0)
    row.kda = float(match.get("kda") or 0)
    row.cs = int(match.get("cs") or 0)
    row.cs_per_min = float(match.get("csPerMin") or 0)
    row.duration_seconds = int(match.get("durationSec") or 0)
    row.win = bool(match.get("isWin"))
    row.queue_id = int(match.get("queueId") or 0)
    row.game_mode = str(match.get("gameMode") or "CLASSIC")[:40]
    row.gold_earned = int(match.get("goldEarned") or 0)
    row.vision_score = int(match.get("visionScore") or 0)
    row.damage_dealt = int(match.get("damageDealt") or 0)
    row.items_json = json.dumps(match.get("items") or [])
    row.trinket_id = int(match.get("trinketId") or 0)
    row.played_at = _played_at(match)
    row.source = source[:20]


def to_ui_match(row: LocalMatchHistory) -> dict[str, Any]:
    try:
        items = json.loads(row.items_json or "[]")
    except json.JSONDecodeError:
        items = []
    if not isinstance(items, list):
        items = []
    played = row.played_at
    timestamp = played.isoformat() if played is not None else None
    return {
        "matchId": row.external_match_id,
        "queueId": row.queue_id,
        "gameMode": row.game_mode,
        "isWin": row.win,
        "championName": row.champion_name,
        "role": row.role,
        "kills": row.kills,
        "deaths": row.deaths,
        "assists": row.assists,
        "kda": row.kda,
        "cs": row.cs,
        "csPerMin": row.cs_per_min,
        "durationSec": row.duration_seconds,
        "timestamp": timestamp,
        "items": [int(item) for item in items if isinstance(item, (int, float))],
        "trinketId": row.trinket_id,
        "goldEarned": row.gold_earned,
        "visionScore": row.vision_score,
        "damageDealt": row.damage_dealt,
    }


async def save_local_matches(
    db: AsyncSession,
    user_id: int,
    matches: list[dict[str, Any]],
    source: str = "lcu",
    puuid: Optional[str] = None,
) -> None:
    """Upserts Summoner's Rift rows. Other modes are skipped, never deleted."""
    for match in matches:
        if not is_rift_row(match):
            continue
        external_id = str(match.get("matchId") or "").strip()
        if not external_id:
            continue
        found = await db.execute(
            select(LocalMatchHistory).where(
                LocalMatchHistory.user_id == user_id,
                LocalMatchHistory.external_match_id == external_id,
            )
        )
        row = found.scalar_one_or_none()
        if row is None:
            row = LocalMatchHistory(user_id=user_id, external_match_id=external_id[:40])
            db.add(row)
        _apply(row, match, source)
        if puuid:
            row.riot_puuid = puuid[:80]
    await db.commit()


async def load_local_matches(
    db: AsyncSession, user_id: int, count: int, puuid: Optional[str] = None
) -> list[dict[str, Any]]:
    """
    Stored Summoner's Rift rows, newest first. Same rule as is_rift_row.
    With a puuid only that League profile's games; without one, every game.
    """
    query = select(LocalMatchHistory).where(
        LocalMatchHistory.user_id == user_id,
        LocalMatchHistory.queue_id.in_(SUMMONERS_RIFT_QUEUES)
        | (LocalMatchHistory.game_mode == "CLASSIC"),
    )
    if puuid:
        query = query.where(LocalMatchHistory.riot_puuid == puuid)
    found = await db.execute(query.order_by(LocalMatchHistory.played_at.desc()).limit(count))
    return [to_ui_match(row) for row in found.scalars().all()]


def profile_view(row: RiotProfile, linked_puuid: Optional[str] = None) -> dict[str, Any]:
    try:
        ranked = json.loads(row.ranked_solo_json) if row.ranked_solo_json else None
    except json.JSONDecodeError:
        ranked = None
    return {
        "puuid": row.puuid,
        "gameName": row.game_name,
        "tagLine": row.tag_line,
        "profileIconId": row.profile_icon_id,
        "summonerLevel": row.summoner_level,
        "rankedSolo": ranked if isinstance(ranked, dict) else None,
        "lastSeenAt": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "linked": bool(linked_puuid) and row.puuid == linked_puuid,
    }


async def save_profile(db: AsyncSession, user_id: int, profile: dict[str, Any]) -> None:
    """Remembers a League profile seen in the client, with its latest snapshot."""
    puuid = str(profile.get("puuid") or "").strip()[:80]
    if not puuid:
        return
    found = await db.execute(
        select(RiotProfile).where(RiotProfile.user_id == user_id, RiotProfile.puuid == puuid)
    )
    row = found.scalar_one_or_none()
    if row is None:
        row = RiotProfile(user_id=user_id, puuid=puuid)
        db.add(row)
    # A partial snapshot keeps the values it does not bring.
    if profile.get("gameName"):
        row.game_name = str(profile["gameName"])[:100]
    if profile.get("tagLine"):
        row.tag_line = str(profile["tagLine"])[:20]
    if profile.get("profileIconId") is not None:
        row.profile_icon_id = int(profile["profileIconId"])
    if profile.get("summonerLevel") is not None:
        row.summoner_level = int(profile["summonerLevel"])
    if "rankedSolo" in profile:
        ranked = profile.get("rankedSolo")
        row.ranked_solo_json = json.dumps(ranked) if isinstance(ranked, dict) else None
    row.last_seen_at = utc_now()
    await db.commit()


async def load_profiles(db: AsyncSession, user_id: int) -> list[RiotProfile]:
    """League profiles of the account, most recently seen first."""
    found = await db.execute(
        select(RiotProfile).where(RiotProfile.user_id == user_id).order_by(RiotProfile.last_seen_at.desc())
    )
    return list(found.scalars().all())


async def default_profile(db: AsyncSession, user: Any) -> Optional[str]:
    """The linked Riot ID, else the last profile opened in the client, else None (every game)."""
    linked = getattr(user, "riot_puuid", None)
    if linked:
        return linked
    profiles = await load_profiles(db, user.id)
    return profiles[0].puuid if profiles else None


async def store_client_history(db: AsyncSession, user_id: int, history: "ClientHistory") -> None:
    """Saves the open client's profile and its games on the account."""
    if history.puuid:
        await save_profile(db, user_id, history.profile)
    if history.matches:
        await save_local_matches(db, user_id, history.matches, source="lcu", puuid=history.puuid or None)


async def _fetch_details(game_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Loads several game details in parallel. A failed game is simply left out."""

    async def _one(game_id: str) -> tuple[str, Any]:
        try:
            return game_id, await riot_lcu_service.get_match_history_game(game_id)
        except Exception as exc:
            logger.debug("LCU game %s detail failed: %s", game_id, type(exc).__name__)
            return game_id, None

    fetched = await asyncio.gather(*(_one(game_id) for game_id in game_ids))
    return {game_id: detail for game_id, detail in fetched if isinstance(detail, dict)}


async def read_lcu_history(limit: int = 15) -> Optional[ClientHistory]:
    """
    Reads and maps the open client's recent games, whatever League account is
    open: the analyzer account is not tied to one League account.
    No database access, so the local agent can use it too.
    Returns None when the client is closed.
    """
    try:
        connected = await riot_lcu_service.ensure_connection()
    except Exception as exc:
        logger.info("LCU history skipped: %s", type(exc).__name__)
        return None
    if not connected:
        return None

    try:
        summoner = await riot_lcu_service.get_current_summoner()
        payload = await riot_lcu_service.get_current_match_history()
    except Exception as exc:
        logger.info("LCU history request failed: %s", type(exc).__name__)
        return None
    if payload is None:
        return None

    summoner = summoner if isinstance(summoner, dict) else {}
    puuid = str(summoner.get("puuid") or "")

    pending_ids: list[str] = []
    for game in extract_games(payload):
        current = unwrap_game(game)
        # Known non-Rift games are skipped; thin entries (unknown mode) still need detail.
        if rift_status(current) is False or not needs_detail(current):
            continue
        game_id = game_id_of(current)
        if game_id and len(pending_ids) < limit:
            pending_ids.append(game_id)

    details = await _fetch_details(pending_ids)
    try:
        ranked = solo_from_lcu(await riot_lcu_service.get_current_ranked_stats())
    except Exception as exc:
        logger.debug("LCU ranked check skipped: %s", type(exc).__name__)
        ranked = None
    profile = {
        "puuid": puuid,
        "gameName": summoner.get("gameName"),
        "tagLine": summoner.get("tagLine"),
        "profileIconId": summoner.get("profileIconId"),
        "summonerLevel": summoner.get("summonerLevel"),
        "rankedSolo": ranked,
    }
    return ClientHistory(puuid, map_lcu_history(payload, puuid, details)[:limit], profile)


async def sync_lcu_history(db: AsyncSession, user: Any, limit: int = 15) -> Optional[ClientHistory]:
    """
    Stores the open client's profile and games on the account (full mode,
    where the backend runs next to the client). Returns None when the client
    is closed. It never links or unlinks a Riot ID.
    """
    history = await read_lcu_history(limit)
    if history is None:
        return None
    await store_client_history(db, user.id, history)
    return history
