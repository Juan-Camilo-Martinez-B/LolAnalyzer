"""
Reads match history from the local League client and stores the UI summary.

The Riot API is not required for this path. A later request can serve the
stored rows after the client closes or the development key expires.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LocalMatchHistory
from app.services.lcu_match_map import extract_games, game_id_of, map_lcu_history, needs_detail, unwrap_game
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
    for match in matches:
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
    found = await db.execute(
        select(LocalMatchHistory)
        .where(LocalMatchHistory.user_id == user_id)
        .order_by(LocalMatchHistory.played_at.desc())
        .limit(count)
    )
    return [to_ui_match(row) for row in found.scalars().all()]


async def sync_lcu_history(db: AsyncSession, user: Any, limit: int = 15) -> Optional[list[dict[str, Any]]]:
    """
    Returns mapped games when the local client answers.
    Returns None when the client is closed or the logged-in account differs.
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

    client_puuid = (summoner or {}).get("puuid") if isinstance(summoner, dict) else None
    linked = getattr(user, "riot_puuid", None)
    if client_puuid and linked and client_puuid != linked:
        logger.info("Local client account does not match the linked Riot account.")
        return None
    puuid = client_puuid or linked or ""

    details: dict[str, dict[str, Any]] = {}
    pending = 0
    for game in extract_games(payload):
        current = unwrap_game(game)
        if not needs_detail(current):
            continue
        game_id = game_id_of(current)
        if not game_id or pending >= limit:
            continue
        pending += 1
        detail = await riot_lcu_service.get_match_history_game(game_id)
        if isinstance(detail, dict):
            details[game_id] = detail

    mapped = map_lcu_history(payload, puuid, details)[:limit]
    if mapped:
        await save_local_matches(db, user.id, mapped, source="lcu")
    return mapped
