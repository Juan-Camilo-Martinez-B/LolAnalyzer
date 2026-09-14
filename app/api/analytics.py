"""
LolAnalyzer Backend - Visual Analytics & Chart Data REST API
Provides data pipelines for interactive visual representations:
- Line Chart: CS Progression vs Challenger Target
- Heatmap / Bar Chart: Death & Tilt distribution across 5-minute intervals
- Radar Chart: 5-Axis tactical skill evaluation (Farming, Combat, Vision, Objectives, Survivability)
- Donut Chart: AI Tactical Coach compliance vs win-rate efficacy
"""

from collections import defaultdict
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.db.models import MatchRecord, MatchTelemetryPoint, User
from app.db.session import get_db
from app.schemas.analytics import (
    CoachImpactChartData,
    CoachImpactSlice,
    CSProgressionChartData,
    CSProgressionPoint,
    RadarAxisScore,
    RoleRadarChartData,
    TiltHeatmapBucket,
    TiltHeatmapChartData,
)

logger = logging.getLogger("lol_analyzer.analytics")

router = APIRouter(prefix="/api/analytics", tags=["Visual Analytics & Charts"])


@router.get("/cs-progression/{match_id}", response_model=CSProgressionChartData)
async def get_cs_progression(
    match_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CSProgressionChartData:
    """
    Returns time-series CS progression data points compared against Challenger benchmarks
    and annotated with AI Coach advice events.
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

    duration_min = max(1.0, round(match.duration_seconds / 60.0, 1))
    final_cs_pm = round(match.cs / duration_min, 2)

    points: List[CSProgressionPoint] = []
    if match.telemetry_points:
        sorted_telemetry = sorted(match.telemetry_points, key=lambda p: p.game_time_seconds)
        for tp in sorted_telemetry:
            minute = round(tp.game_time_seconds / 60.0, 1)
            target = max(0, int(round(10.0 * max(0.0, minute - 1.5))))
            is_crash = (
                tp.cs_per_minute < 5.0 and minute >= 5.0 and match.role.upper() != "SUPPORT"
            )
            points.append(
                CSProgressionPoint(
                    timestamp_minute=minute,
                    actual_cs=tp.cs,
                    actual_cs_per_min=round(tp.cs_per_minute, 2),
                    challenger_target_cs=target,
                    has_cs_crash=is_crash,
                    advice_text=tp.advice_text,
                )
            )
    else:
        # Synthesize baseline points if none were recorded
        intervals = [0.0, min(10.0, duration_min / 2), duration_min]
        for minute in intervals:
            cs_estimate = int(round((minute / duration_min) * match.cs))
            cs_pm = round(cs_estimate / max(1.0, minute), 2)
            target = max(0, int(round(10.0 * max(0.0, minute - 1.5))))
            points.append(
                CSProgressionPoint(
                    timestamp_minute=minute,
                    actual_cs=cs_estimate,
                    actual_cs_per_min=cs_pm,
                    challenger_target_cs=target,
                    has_cs_crash=cs_pm < 5.0 and minute >= 5.0 and match.role.upper() != "SUPPORT",
                )
            )

    return CSProgressionChartData(
        match_id=match.id,
        champion_name=match.champion_name,
        role=match.role,
        duration_minutes=duration_min,
        total_cs=match.cs,
        final_cs_per_minute=final_cs_pm,
        points=points,
    )


@router.get("/tilt-heatmap", response_model=TiltHeatmapChartData)
async def get_tilt_heatmap(
    limit_matches: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TiltHeatmapChartData:
    """
    Analyzes user death timing and tilt trigger distribution across 5-minute intervals
    to locate peak vulnerability phases (Early, Mid, or Late game).
    """
    stmt = (
        select(MatchRecord)
        .options(selectinload(MatchRecord.telemetry_points))
        .where(MatchRecord.user_id == current_user.id)
        .order_by(MatchRecord.played_at.desc())
        .limit(limit_matches)
    )
    result = await db.execute(stmt)
    matches = result.scalars().all()

    intervals = [
        ("0-5m", 0, 300),
        ("5-10m", 300, 600),
        ("10-15m", 600, 900),
        ("15-20m", 900, 1200),
        ("20-25m", 1200, 1500),
        ("25+m", 1500, 999999),
    ]

    bucket_deaths: Dict[str, int] = defaultdict(int)
    bucket_tilts: Dict[str, int] = defaultdict(int)

    total_deaths = 0
    total_tilt_triggers = 0

    for match in matches:
        total_deaths += match.deaths
        total_tilt_triggers += match.tilt_triggers_count

        if match.telemetry_points:
            for tp in match.telemetry_points:
                t = tp.game_time_seconds
                for label, start_s, end_s in intervals:
                    if start_s <= t < end_s:
                        if tp.deaths > 0:
                            bucket_deaths[label] += tp.deaths
                        if tp.advice_text and "tilt" in tp.advice_text.lower():
                            bucket_tilts[label] += 1
                        break
        else:
            # Distribute proportionally by duration
            dur = match.duration_seconds
            for label, start_s, end_s in intervals:
                if start_s < dur:
                    portion = min(dur, end_s) - start_s
                    weight = portion / max(1.0, dur)
                    bucket_deaths[label] += int(round(match.deaths * weight))
                    bucket_tilts[label] += int(round(match.tilt_triggers_count * weight))

    # Build bucket list
    buckets: List[TiltHeatmapBucket] = []
    peak_label = "N/A"
    max_deaths = -1

    for label, _, _ in intervals:
        deaths = bucket_deaths[label]
        tilts = bucket_tilts[label]
        pct = round((deaths / max(1, total_deaths)) * 100, 1) if total_deaths > 0 else 0.0

        if deaths > max_deaths and total_deaths > 0:
            max_deaths = deaths
            peak_label = label

        buckets.append(
            TiltHeatmapBucket(
                time_bucket=label,
                deaths_count=deaths,
                tilt_triggers_count=tilts,
                deaths_percentage=pct,
            )
        )

    return TiltHeatmapChartData(
        total_matches_analyzed=len(matches),
        total_deaths=total_deaths,
        total_tilt_triggers=total_tilt_triggers,
        peak_danger_interval=peak_label if total_deaths > 0 else "0-5m",
        buckets=buckets,
    )


@router.get("/role-radar", response_model=RoleRadarChartData)
async def get_role_radar(
    role: Optional[str] = Query(default=None, description="Optional role filter (e.g. MID, TOP, ADC)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoleRadarChartData:
    """
    Computes a 5-axis tactical radar score (Farming, Combat, Vision, Objectives, Survivability)
    normalized between 0-100 and compared with Challenger tier baselines.
    """
    stmt = select(MatchRecord).where(MatchRecord.user_id == current_user.id)
    if role:
        stmt = stmt.where(MatchRecord.role == role.strip().upper())
    result = await db.execute(stmt)
    matches = result.scalars().all()

    effective_role = role.upper() if role else "ALL"
    if not matches:
        return RoleRadarChartData(
            role=effective_role,
            matches_evaluated=0,
            overall_performance_rating="N/A",
            scores=[
                RadarAxisScore(axis="Farming", score=50.0, benchmark_score=80.0),
                RadarAxisScore(axis="Combat", score=50.0, benchmark_score=80.0),
                RadarAxisScore(axis="Vision & Safety", score=50.0, benchmark_score=80.0),
                RadarAxisScore(axis="Objectives & Gold", score=50.0, benchmark_score=80.0),
                RadarAxisScore(axis="Survivability", score=50.0, benchmark_score=80.0),
            ],
        )

    count = len(matches)

    # 1. Farming Score (target: 10 CS/min = 100%)
    avg_cs_pm = sum(m.cs / max(1.0, m.duration_seconds / 60.0) for m in matches) / count
    farming_score = min(100.0, round((avg_cs_pm / 10.0) * 100, 1))

    # 2. Combat Score (target: KDA >= 4.0 -> 100%)
    total_k = sum(m.kills for m in matches)
    total_d = sum(m.deaths for m in matches)
    total_a = sum(m.assists for m in matches)
    avg_kda = (total_k + total_a) / max(1, total_d)
    combat_score = min(100.0, round((avg_kda / 4.0) * 100, 1))

    # 3. Vision & Safety (based on low deaths per 10 minutes)
    total_duration_10m = sum(m.duration_seconds for m in matches) / 600.0
    deaths_per_10m = total_d / max(1.0, total_duration_10m)
    # ideal <= 1.0 death/10min (100%), 3.0 deaths/10min = 50%
    vision_safety_score = max(0.0, min(100.0, round(100.0 - (deaths_per_10m * 18.0), 1)))

    # 4. Objectives & Gold (target: average gold diff >= 2000 -> 100%)
    avg_gold_diff = sum(m.gold_difference for m in matches) / count
    obj_score = max(0.0, min(100.0, round(50.0 + (avg_gold_diff / 50.0), 1)))

    # 5. Survivability / Tilt Resilience
    total_tilts = sum(m.tilt_triggers_count for m in matches)
    avg_tilt_pm = total_tilts / count
    survivability_score = max(0.0, min(100.0, round(100.0 - (avg_tilt_pm * 25.0), 1)))

    overall_avg = (
        farming_score + combat_score + vision_safety_score + obj_score + survivability_score
    ) / 5.0

    if overall_avg >= 85:
        rating = "S+"
    elif overall_avg >= 75:
        rating = "S"
    elif overall_avg >= 65:
        rating = "A"
    elif overall_avg >= 50:
        rating = "B"
    else:
        rating = "C"

    scores = [
        RadarAxisScore(axis="Farming", score=farming_score, benchmark_score=85.0),
        RadarAxisScore(axis="Combat", score=combat_score, benchmark_score=80.0),
        RadarAxisScore(axis="Vision & Safety", score=vision_safety_score, benchmark_score=80.0),
        RadarAxisScore(axis="Objectives & Gold", score=obj_score, benchmark_score=75.0),
        RadarAxisScore(axis="Survivability", score=survivability_score, benchmark_score=80.0),
    ]

    return RoleRadarChartData(
        role=effective_role,
        matches_evaluated=count,
        overall_performance_rating=rating,
        scores=scores,
    )


@router.get("/coach-impact", response_model=CoachImpactChartData)
async def get_coach_impact(
    limit_matches: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CoachImpactChartData:
    """
    Evaluates the quantifiable impact of following AI Tactical Coach recommendations
    on match victory rate (Donut Chart visualization).
    """
    stmt = (
        select(MatchRecord)
        .where(MatchRecord.user_id == current_user.id)
        .order_by(MatchRecord.played_at.desc())
        .limit(limit_matches)
    )
    result = await db.execute(stmt)
    matches = result.scalars().all()

    total_advices = sum(m.advices_received_count for m in matches)
    total_followed = sum(m.advices_followed_count for m in matches)
    compliance_pct = (
        round((total_followed / max(1, total_advices)) * 100, 1) if total_advices > 0 else 0.0
    )

    followed_won = 0
    followed_lost = 0
    ignored_won = 0
    ignored_lost = 0

    for m in matches:
        if m.advices_received_count == 0:
            continue
        # Complied if followed majority of advices
        if m.advices_followed_count >= (m.advices_received_count / 2.0):
            if m.win:
                followed_won += 1
            else:
                followed_lost += 1
        else:
            if m.win:
                ignored_won += 1
            else:
                ignored_lost += 1

    total_slices = followed_won + followed_lost + ignored_won + ignored_lost
    def _pct(c: int) -> float:
        return round((c / max(1, total_slices)) * 100, 1) if total_slices > 0 else 0.0

    slices = [
        CoachImpactSlice(category="Followed & Won", count=followed_won, percentage=_pct(followed_won)),
        CoachImpactSlice(category="Followed & Lost", count=followed_lost, percentage=_pct(followed_lost)),
        CoachImpactSlice(category="Ignored & Won", count=ignored_won, percentage=_pct(ignored_won)),
        CoachImpactSlice(category="Ignored & Lost", count=ignored_lost, percentage=_pct(ignored_lost)),
    ]

    total_followed_games = followed_won + followed_lost
    total_ignored_games = ignored_won + ignored_lost

    wr_followed = (
        round((followed_won / max(1, total_followed_games)) * 100, 1)
        if total_followed_games > 0
        else 0.0
    )
    wr_ignored = (
        round((ignored_won / max(1, total_ignored_games)) * 100, 1)
        if total_ignored_games > 0
        else 0.0
    )
    delta = round(wr_followed - wr_ignored, 1)

    return CoachImpactChartData(
        total_advices_received=total_advices,
        total_advices_followed=total_followed,
        compliance_rate_percentage=compliance_pct,
        winrate_when_followed=wr_followed,
        winrate_when_ignored=wr_ignored,
        coach_efficacy_delta=delta,
        slices=slices,
    )
