"""
LolAnalyzer Backend - Match Statistics & History REST API
Provides match logging, time-series telemetry persistence, aggregate performance summaries,
filtered paginated history, and symmetrical match reset operations.
"""

from collections import defaultdict
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.db.models import MatchRecord, MatchTelemetryPoint, User
from app.db.session import get_db
from app.schemas.stats import (
    ChampionStatSummary,
    MatchCreate,
    MatchDetailResponse,
    MatchListItem,
    PaginatedMatchesResponse,
    ResetStatsResponse,
    RoleStatSummary,
    StatsSummary,
    TelemetryPointResponse,
)

logger = logging.getLogger("lol_analyzer.stats")

router = APIRouter(prefix="/api/stats", tags=["Statistics & Matches"])


def _calculate_kda(kills: int, deaths: int, assists: int) -> float:
    """Calculates KDA ratio avoiding division by zero."""
    return round((kills + assists) / max(1, deaths), 2)


def _calculate_cs_per_min(cs: int, duration_seconds: int) -> float:
    """Calculates CS per minute."""
    minutes = max(1.0, duration_seconds / 60.0)
    return round(cs / minutes, 2)


def _to_match_list_item(match: MatchRecord) -> MatchListItem:
    """Helper to convert MatchRecord ORM model to MatchListItem schema."""
    return MatchListItem(
        id=match.id,
        game_id=match.game_id,
        champion_name=match.champion_name,
        role=match.role,
        kills=match.kills,
        deaths=match.deaths,
        assists=match.assists,
        kda_ratio=_calculate_kda(match.kills, match.deaths, match.assists),
        cs=match.cs,
        cs_per_minute=_calculate_cs_per_min(match.cs, match.duration_seconds),
        gold_earned=match.gold_earned,
        gold_difference=match.gold_difference,
        duration_seconds=match.duration_seconds,
        win=match.win,
        tilt_triggers_count=match.tilt_triggers_count,
        advices_received_count=match.advices_received_count,
        advices_followed_count=match.advices_followed_count,
        played_at=match.played_at,
    )


def _to_match_detail(match: MatchRecord) -> MatchDetailResponse:
    """Helper to convert MatchRecord ORM model to MatchDetailResponse schema."""
    telemetry_responses = [
        TelemetryPointResponse(
            id=tp.id,
            match_id=tp.match_id,
            game_time_seconds=tp.game_time_seconds,
            cs=tp.cs,
            cs_per_minute=tp.cs_per_minute,
            kills=tp.kills,
            deaths=tp.deaths,
            flash_ready=tp.flash_ready,
            advice_text=tp.advice_text,
        )
        for tp in (match.telemetry_points or [])
    ]

    return MatchDetailResponse(
        id=match.id,
        user_id=match.user_id,
        game_id=match.game_id,
        champion_name=match.champion_name,
        role=match.role,
        kills=match.kills,
        deaths=match.deaths,
        assists=match.assists,
        kda_ratio=_calculate_kda(match.kills, match.deaths, match.assists),
        cs=match.cs,
        cs_per_minute=_calculate_cs_per_min(match.cs, match.duration_seconds),
        gold_earned=match.gold_earned,
        gold_difference=match.gold_difference,
        duration_seconds=match.duration_seconds,
        win=match.win,
        tilt_triggers_count=match.tilt_triggers_count,
        advices_received_count=match.advices_received_count,
        advices_followed_count=match.advices_followed_count,
        played_at=match.played_at,
        telemetry_points=telemetry_responses,
    )


@router.post("/matches", response_model=MatchDetailResponse, status_code=status.HTTP_201_CREATED)
async def record_match(
    match_in: MatchCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchDetailResponse:
    """
    Records a completed match with full summary metrics and granular time-series telemetry points.
    """
    new_match = MatchRecord(
        user_id=current_user.id,
        game_id=match_in.game_id,
        champion_name=match_in.champion_name.strip(),
        role=match_in.role.upper().strip(),
        kills=match_in.kills,
        deaths=match_in.deaths,
        assists=match_in.assists,
        cs=match_in.cs,
        gold_earned=match_in.gold_earned,
        gold_difference=match_in.gold_difference,
        duration_seconds=match_in.duration_seconds,
        win=match_in.win,
        tilt_triggers_count=match_in.tilt_triggers_count,
        advices_received_count=match_in.advices_received_count,
        advices_followed_count=match_in.advices_followed_count,
    )
    db.add(new_match)
    await db.flush()  # Populates new_match.id

    for tp in match_in.telemetry_points:
        telemetry_point = MatchTelemetryPoint(
            match_id=new_match.id,
            game_time_seconds=tp.game_time_seconds,
            cs=tp.cs,
            cs_per_minute=tp.cs_per_minute,
            kills=tp.kills,
            deaths=tp.deaths,
            flash_ready=tp.flash_ready,
            advice_text=tp.advice_text,
        )
        db.add(telemetry_point)

    await db.commit()
    await db.refresh(new_match)

    logger.info(
        f"Match recorded for user {current_user.email}: Match ID {new_match.id}, "
        f"Champ: {new_match.champion_name}, Win: {new_match.win}"
    )
    return _to_match_detail(new_match)


@router.get("/summary", response_model=StatsSummary)
async def get_stats_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StatsSummary:
    """
    Computes overall career statistics, winrates per role, KDA, CS metrics,
    tilt frequency, AI coach compliance rate, and top champions for the user.
    """
    stmt = (
        select(MatchRecord)
        .where(MatchRecord.user_id == current_user.id)
        .order_by(MatchRecord.played_at.desc())
    )
    result = await db.execute(stmt)
    matches = result.scalars().all()

    total_matches = len(matches)
    if total_matches == 0:
        return StatsSummary(
            total_matches=0,
            total_wins=0,
            total_losses=0,
            overall_winrate_percentage=0.0,
            total_kills=0,
            total_deaths=0,
            total_assists=0,
            average_kda=0.0,
            average_cs=0.0,
            average_cs_per_minute=0.0,
            total_duration_minutes=0.0,
            total_tilt_triggers=0,
            avg_tilt_triggers_per_match=0.0,
            total_coach_advices=0,
            total_coach_advices_followed=0,
            coach_compliance_rate_percentage=0.0,
            role_stats={},
            top_champions=[],
        )

    wins = sum(1 for m in matches if m.win)
    losses = total_matches - wins
    overall_winrate = round((wins / total_matches) * 100, 1)

    total_kills = sum(m.kills for m in matches)
    total_deaths = sum(m.deaths for m in matches)
    total_assists = sum(m.assists for m in matches)
    avg_kda = _calculate_kda(total_kills, total_deaths, total_assists)

    total_cs = sum(m.cs for m in matches)
    avg_cs = round(total_cs / total_matches, 1)

    total_duration_seconds = sum(m.duration_seconds for m in matches)
    total_duration_minutes = round(total_duration_seconds / 60.0, 1)

    avg_cs_pm = round(
        sum(_calculate_cs_per_min(m.cs, m.duration_seconds) for m in matches) / total_matches, 2
    )

    total_tilt = sum(m.tilt_triggers_count for m in matches)
    avg_tilt = round(total_tilt / total_matches, 2)

    total_advices = sum(m.advices_received_count for m in matches)
    total_followed = sum(m.advices_followed_count for m in matches)
    compliance_rate = (
        round((total_followed / total_advices) * 100, 1) if total_advices > 0 else 0.0
    )

    # Role breakdown
    role_map: Dict[str, Dict[str, int]] = defaultdict(lambda: {"games": 0, "wins": 0, "losses": 0})
    # Champion breakdown
    champ_map: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "kills": 0, "deaths": 0, "assists": 0}
    )

    for m in matches:
        # Role
        role_map[m.role]["games"] += 1
        if m.win:
            role_map[m.role]["wins"] += 1
        else:
            role_map[m.role]["losses"] += 1

        # Champ
        champ_map[m.champion_name]["games"] += 1
        if m.win:
            champ_map[m.champion_name]["wins"] += 1
        else:
            champ_map[m.champion_name]["losses"] += 1
        champ_map[m.champion_name]["kills"] += m.kills
        champ_map[m.champion_name]["deaths"] += m.deaths
        champ_map[m.champion_name]["assists"] += m.assists

    role_stats = {
        role: RoleStatSummary(
            games=data["games"],
            wins=data["wins"],
            losses=data["losses"],
            winrate_percentage=round((data["wins"] / data["games"]) * 100, 1),
        )
        for role, data in role_map.items()
    }

    top_champions = [
        ChampionStatSummary(
            champion_name=champ,
            games=data["games"],
            wins=data["wins"],
            losses=data["losses"],
            winrate_percentage=round((data["wins"] / data["games"]) * 100, 1),
            avg_kda=_calculate_kda(data["kills"], data["deaths"], data["assists"]),
        )
        for champ, data in champ_map.items()
    ]
    top_champions.sort(key=lambda c: c.games, reverse=True)

    return StatsSummary(
        total_matches=total_matches,
        total_wins=wins,
        total_losses=losses,
        overall_winrate_percentage=overall_winrate,
        total_kills=total_kills,
        total_deaths=total_deaths,
        total_assists=total_assists,
        average_kda=avg_kda,
        average_cs=avg_cs,
        average_cs_per_minute=avg_cs_pm,
        total_duration_minutes=total_duration_minutes,
        total_tilt_triggers=total_tilt,
        avg_tilt_triggers_per_match=avg_tilt,
        total_coach_advices=total_advices,
        total_coach_advices_followed=total_followed,
        coach_compliance_rate_percentage=compliance_rate,
        role_stats=role_stats,
        top_champions=top_champions,
    )


@router.get("/matches", response_model=PaginatedMatchesResponse)
async def list_matches(
    champion: Optional[str] = Query(default=None, description="Filter by champion name"),
    role: Optional[str] = Query(default=None, description="Filter by role (e.g. TOP, MID, ADC)"),
    win: Optional[bool] = Query(default=None, description="Filter by victory (true) or defeat (false)"),
    limit: int = Query(default=20, ge=1, le=100),
    skip: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedMatchesResponse:
    """
    Returns a paginated list of the user's recorded matches with optional filters.
    """
    stmt = select(MatchRecord).where(MatchRecord.user_id == current_user.id)

    if champion:
        stmt = stmt.where(MatchRecord.champion_name.ilike(f"%{champion.strip()}%"))
    if role:
        stmt = stmt.where(MatchRecord.role == role.strip().upper())
    if win is not None:
        stmt = stmt.where(MatchRecord.win == win)

    # Count total matching
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_res = await db.execute(count_stmt)
    total_count = count_res.scalar_one()

    # Paginate and order by newest first
    stmt = stmt.order_by(MatchRecord.played_at.desc()).offset(skip).limit(limit)
    res = await db.execute(stmt)
    matches = res.scalars().all()

    return PaginatedMatchesResponse(
        total_count=total_count,
        limit=limit,
        skip=skip,
        matches=[_to_match_list_item(m) for m in matches],
    )


@router.get("/matches/{match_id}", response_model=MatchDetailResponse)
async def get_match_detail(
    match_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchDetailResponse:
    """
    Returns full details for a specific match including its fine-grained time-series telemetry points.
    """
    stmt = (
        select(MatchRecord)
        .options(selectinload(MatchRecord.telemetry_points))
        .where(MatchRecord.id == match_id, MatchRecord.user_id == current_user.id)
    )
    result = await db.execute(stmt)
    match = result.scalar_one_or_none()

    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Match record #{match_id} not found.",
        )

    return _to_match_detail(match)


@router.delete("/matches/{match_id}")
async def delete_match(
    match_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Deletes a single match record and cascades deletion to all its telemetry points.
    """
    stmt = select(MatchRecord).where(
        MatchRecord.id == match_id, MatchRecord.user_id == current_user.id
    )
    result = await db.execute(stmt)
    match = result.scalar_one_or_none()

    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Match record #{match_id} not found.",
        )

    await db.delete(match)
    await db.commit()

    logger.info(f"Match #{match_id} deleted by user {current_user.email}")
    return {
        "status": "deleted",
        "match_id": match_id,
        "message": f"Match record #{match_id} deleted successfully.",
    }


@router.delete("/reset", response_model=ResetStatsResponse)
async def reset_user_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResetStatsResponse:
    """
    Permanently clears and resets all recorded match history and telemetry points for the authenticated user.
    """
    # Count before deleting
    count_stmt = select(func.count(MatchRecord.id)).where(MatchRecord.user_id == current_user.id)
    count_res = await db.execute(count_stmt)
    total_to_delete = count_res.scalar_one()

    # Delete all matches for user
    stmt = delete(MatchRecord).where(MatchRecord.user_id == current_user.id)
    await db.execute(stmt)
    await db.commit()

    logger.warning(
        f"Reset all stats for user {current_user.email} (Deleted {total_to_delete} matches)"
    )
    return ResetStatsResponse(
        status="stats_reset",
        message=f"All {total_to_delete} match records have been cleared from your history.",
        deleted_matches_count=total_to_delete,
    )
