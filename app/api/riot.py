"""
Riot account link and on-demand public data.
Match history prefers the local League client and is stored in local_match_history.
The Riot match-v5 API is only a fallback when that archive is empty.
"""

from datetime import datetime, timezone
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.champion_catalog import get_champion_catalog
from app.services.lcu_match_map import QUEUE_LABELS, is_summoners_rift
from app.services.local_match_history import load_local_matches, save_local_matches, sync_lcu_history
from app.services.riot_lcu import riot_lcu_service
from app.services.riot_api import (
    PLATFORM_TO_REGION,
    RiotUnavailable,
    account_by_riot_id,
    league_entries,
    match_detail,
    raise_riot,
    recent_match_ids,
    summoner_by_puuid,
)

router = APIRouter(prefix="/api/riot", tags=["Riot"])
logger = logging.getLogger("lol_analyzer.riot_routes")


class RiotConnectRequest(BaseModel):
    game_name: str = Field(..., min_length=1, max_length=40)
    tag_line: str = Field(..., min_length=1, max_length=10)
    region: str = Field(default="la1", min_length=2, max_length=10)


def _queue_label(queue_id: int, fallback: str | None) -> str:
    return QUEUE_LABELS.get(queue_id) or fallback or "CLASSIC"


async def _local_summoner() -> dict[str, Any] | None:
    """Account open in the local League client, or None when it is closed."""
    try:
        if not await riot_lcu_service.ensure_connection():
            return None
        summoner = await riot_lcu_service.get_current_summoner()
    except Exception as exc:
        logger.debug("LCU summoner check skipped: %s", type(exc).__name__)
        return None
    if isinstance(summoner, dict) and summoner.get("puuid"):
        return summoner
    return None


async def _local_ranked_stats() -> dict[str, Any] | None:
    try:
        return await riot_lcu_service.get_current_ranked_stats()
    except Exception as exc:
        logger.debug("LCU ranked check skipped: %s", type(exc).__name__)
        return None


def _same_riot_id(summoner: dict[str, Any], game_name: str, tag_line: str) -> bool:
    local_name = str(summoner.get("gameName") or "").strip().casefold()
    local_tag = str(summoner.get("tagLine") or "").strip().casefold()
    return bool(local_name) and local_name == game_name.casefold() and local_tag == tag_line.casefold()


def _solo_from_lcu(ranked_stats: dict[str, Any] | None) -> dict[str, Any] | None:
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


def _role(position: str | None) -> str:
    mapped = {"BOTTOM": "ADC", "UTILITY": "SUPPORT", "": "UNKNOWN"}.get(position or "", position or "UNKNOWN")
    if mapped not in {"TOP", "JUNGLE", "MID", "ADC", "SUPPORT", "FILL", "UNKNOWN"}:
        return "UNKNOWN"
    return mapped


def _map_match(payload: dict[str, Any], puuid: str) -> dict[str, Any]:
    info = payload.get("info") or {}
    participant = next((item for item in info.get("participants", []) if item.get("puuid") == puuid), None)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Match payload did not include the linked player.")

    kills = int(participant.get("kills") or 0)
    deaths = int(participant.get("deaths") or 0)
    assists = int(participant.get("assists") or 0)
    duration = int(info.get("gameDuration") or 0)
    cs = int(participant.get("totalMinionsKilled") or 0) + int(participant.get("neutralMinionsKilled") or 0)
    created = info.get("gameCreation")
    timestamp = None
    if created:
        timestamp = datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat()
    queue_id = int(info.get("queueId") or 0)
    items = [int(participant.get(f"item{index}") or 0) for index in range(6)]
    return {
        "matchId": (payload.get("metadata") or {}).get("matchId"),
        "queueId": queue_id,
        "gameMode": _queue_label(queue_id, info.get("gameMode")),
        "isWin": bool(participant.get("win")),
        "championName": participant.get("championName") or "Unknown",
        "role": _role(participant.get("teamPosition")),
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kda": round((kills + assists) / deaths, 2) if deaths else float(kills + assists),
        "cs": cs,
        "csPerMin": round(cs / (duration / 60), 2) if duration else 0,
        "durationSec": duration,
        "timestamp": timestamp,
        "items": items,
        "trinketId": int(participant.get("item6") or 0),
        "goldEarned": int(participant.get("goldEarned") or 0),
        "visionScore": int(participant.get("visionScore") or 0),
        "damageDealt": int(participant.get("totalDamageDealtToChampions") or 0),
    }


def _require_link(user: User) -> str:
    if not user.riot_puuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Riot account is linked yet.",
        )
    return user.riot_puuid


@router.get("/champions")
async def champions(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return {"champions": await get_champion_catalog(db)}


@router.get("/regions")
async def regions() -> dict[str, list[str]]:
    return {"regions": sorted(PLATFORM_TO_REGION)}


@router.post("/connect")
async def connect_riot(
    body: RiotConnectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    platform = body.region.lower().strip()
    if platform not in PLATFORM_TO_REGION:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported region.")

    game_name = body.game_name.strip()
    tag_line = body.tag_line.strip()
    puuid: str | None = None
    icon_id: Any = None

    # 1. El cliente local solo cuenta si su Riot ID es exactamente el pedido.
    local_summoner = await _local_summoner()
    if local_summoner and _same_riot_id(local_summoner, game_name, tag_line):
        puuid = local_summoner["puuid"]
        game_name = local_summoner.get("gameName") or game_name
        tag_line = local_summoner.get("tagLine") or tag_line
        icon_id = local_summoner.get("profileIconId")

    # 2. Si no, la API oficial de Riot resuelve el Riot ID.
    if not puuid:
        try:
            account = await account_by_riot_id(db, game_name, tag_line, platform)
        except RiotUnavailable as error:
            raise_riot(error)
        puuid = account.get("puuid")
        game_name = account.get("gameName") or game_name
        tag_line = account.get("tagLine") or tag_line

    if not puuid:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Riot account response did not include a puuid.")

    taken = await db.execute(select(User).where(User.riot_puuid == puuid, User.id != current_user.id))
    if taken.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That Riot account is already linked to another user.")

    if icon_id is None:
        try:
            summoner = await summoner_by_puuid(db, platform, puuid)
            icon_id = summoner.get("profileIconId")
        except RiotUnavailable as error:
            if error.status_code != status.HTTP_404_NOT_FOUND:
                raise_riot(error)

    current_user.riot_puuid = puuid
    current_user.riot_game_name = game_name
    current_user.riot_tag_line = tag_line
    current_user.region = platform
    current_user.summoner_name = f"{current_user.riot_game_name}#{current_user.riot_tag_line}"
    if icon_id is not None:
        current_user.summoner_icon_id = int(icon_id)
    await db.commit()
    return {
        "linked": True,
        "game_name": current_user.riot_game_name,
        "tag_line": current_user.riot_tag_line,
        "region": current_user.region,
        "puuid": current_user.riot_puuid,
    }


@router.delete("/connect")
async def disconnect_riot(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    current_user.riot_puuid = None
    current_user.riot_game_name = None
    current_user.riot_tag_line = None
    await db.commit()
    return {"linked": False}


@router.get("/profile")
async def riot_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    puuid = _require_link(current_user)
    platform = current_user.region or "la1"

    # 1. Si el cliente abierto es la cuenta vinculada, se lee de ahi sin la API.
    local_summoner = await _local_summoner()
    if local_summoner and local_summoner.get("puuid") == puuid:
        ranked_stats = await _local_ranked_stats()
        return {
            "puuid": puuid,
            "gameName": current_user.riot_game_name or local_summoner.get("gameName"),
            "tagLine": current_user.riot_tag_line or local_summoner.get("tagLine"),
            "region": platform,
            "summonerLevel": local_summoner.get("summonerLevel"),
            "profileIconId": local_summoner.get("profileIconId") or current_user.summoner_icon_id,
            "rankedSolo": _solo_from_lcu(ranked_stats),
        }

    # 2. Cliente cerrado u otra cuenta abierta: API oficial de Riot.
    ranked: list[dict[str, Any]] = []
    summoner: dict[str, Any] = {}
    try:
        summoner = await summoner_by_puuid(db, platform, puuid)
        ranked = await league_entries(db, platform, puuid)
    except RiotUnavailable as error:
        # 404: cuenta sin rango. 503: sin clave configurada; se muestra la cuenta sin rango.
        if error.status_code not in (status.HTTP_404_NOT_FOUND, status.HTTP_503_SERVICE_UNAVAILABLE):
            raise_riot(error)

    solo = next((entry for entry in ranked if entry.get("queueType") == "RANKED_SOLO_5x5"), None)
    return {
        "puuid": puuid,
        "gameName": current_user.riot_game_name,
        "tagLine": current_user.riot_tag_line,
        "region": platform,
        "summonerLevel": summoner.get("summonerLevel"),
        "profileIconId": summoner.get("profileIconId") or current_user.summoner_icon_id,
        "rankedSolo": solo,
    }


@router.get("/matches")
async def riot_matches(
    count: int = Query(default=10, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    # 1. Cliente local, solo si la cuenta abierta es la vinculada.
    try:
        synced = await sync_lcu_history(db, current_user, limit=count)
    except Exception as exc:
        logger.info("Local history sync failed: %s", type(exc).__name__)
        synced = None
    if synced:
        return synced[:count]

    # 2. Historial archivado en local_match_history.
    stored = await load_local_matches(db, current_user.id, count)
    if stored:
        return stored

    # 3. API de Riot como ultimo recurso.
    if not current_user.riot_puuid:
        return []

    puuid = current_user.riot_puuid
    platform = current_user.region or "la1"
    try:
        match_ids = await recent_match_ids(db, platform, puuid, count)
        matches = []
        for match_id in match_ids:
            detail = await match_detail(db, platform, match_id)
            if is_summoners_rift(detail.get("info") or {}):
                matches.append(_map_match(detail, puuid))
        if matches:
            try:
                await save_local_matches(db, current_user.id, matches, source="riot")
            except Exception as exc:
                logger.info("Could not archive Riot matches: %s", type(exc).__name__)
        return matches
    except RiotUnavailable as error:
        raise_riot(error)


@router.get("/stats")
async def riot_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    matches = await riot_matches(count=10, current_user=current_user, db=db)
    games = len(matches)
    if games == 0:
        return {
            "winrate": 0,
            "kda": 0,
            "avgKills": 0,
            "avgDeaths": 0,
            "avgAssists": 0,
            "avgCSPerMin": 0,
            "gamesAnalyzed": 0,
            "tiltIndex": 0,
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
            "champions": [],
        }

    wins = sum(1 for match in matches if match["isWin"])
    champions: dict[str, dict[str, Any]] = {}
    for match in matches:
        row = champions.setdefault(match["championName"], {
            "championName": match["championName"],
            "role": match["role"],
            "games": 0,
            "wins": 0,
            "kdaTotal": 0,
            "csTotal": 0,
        })
        row["games"] += 1
        row["wins"] += 1 if match["isWin"] else 0
        row["kdaTotal"] += match["kda"]
        row["csTotal"] += match["csPerMin"]

    champion_rows = []
    for row in champions.values():
        champion_rows.append({
            "championId": 0,
            "championName": row["championName"],
            "role": row["role"],
            "games": row["games"],
            "wins": row["wins"],
            "winrate": round(row["wins"] / row["games"] * 100, 1),
            "kda": round(row["kdaTotal"] / row["games"], 2),
            "avgCSPerMin": round(row["csTotal"] / row["games"], 2),
            "mastery": 0,
            "masteryLevel": 0,
            "recentTrend": "stable",
        })

    return {
        "winrate": round(wins / games * 100, 1),
        "kda": round(sum(match["kda"] for match in matches) / games, 2),
        "avgKills": round(sum(match["kills"] for match in matches) / games, 2),
        "avgDeaths": round(sum(match["deaths"] for match in matches) / games, 2),
        "avgAssists": round(sum(match["assists"] for match in matches) / games, 2),
        "avgCSPerMin": round(sum(match["csPerMin"] for match in matches) / games, 2),
        "gamesAnalyzed": games,
        "tiltIndex": 0,
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "champions": champion_rows,
    }
