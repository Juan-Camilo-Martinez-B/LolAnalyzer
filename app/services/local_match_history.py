"""
Reads match history from the local League client and stores the UI summary.

The Riot API is not required for this path. A later request can serve the
stored rows after the client closes or the development key expires.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LocalMatchHistory
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
    await db.commit()


async def load_local_matches(db: AsyncSession, user_id: int, count: int) -> list[dict[str, Any]]:
    """Stored Summoner's Rift rows, newest first. Same rule as is_rift_row."""
    found = await db.execute(
        select(LocalMatchHistory)
        .where(
            LocalMatchHistory.user_id == user_id,
            LocalMatchHistory.queue_id.in_(SUMMONERS_RIFT_QUEUES)
            | (LocalMatchHistory.game_mode == "CLASSIC"),
        )
        .order_by(LocalMatchHistory.played_at.desc())
        .limit(count)
    )
    return [to_ui_match(row) for row in found.scalars().all()]


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


async def sync_lcu_history(db: AsyncSession, user: Any, limit: int = 15) -> Optional[list[dict[str, Any]]]:
    """
    Returns mapped games when the local client answers.
    Returns None when the client is closed or the logged-in account differs
    from the linked one. It never links or unlinks accounts.
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

    client_puuid = summoner.get("puuid") if isinstance(summoner, dict) else None
    linked = getattr(user, "riot_puuid", None)
    if client_puuid and linked and client_puuid != linked:
        logger.info("Local client account does not match the linked Riot account.")
        return None
    puuid = client_puuid or linked or ""

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
    mapped = map_lcu_history(payload, puuid, details)[:limit]
    if mapped:
        await save_local_matches(db, user.id, mapped, source="lcu")
    return mapped
