"""
Lane plans kept per account, so the player can read them again after
champion select. The plan itself still comes from matchup_plan (one Groq call
per matchup); this module only stores and lists what each player received.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MatchupReport, utc_now

REPORT_LIMIT = 30


async def save_matchup_report(
    db: AsyncSession, user_id: int, champion: str, opponent: str, lane: str, plan: dict[str, str]
) -> None:
    """Stores or refreshes the plan for this matchup. Repeats never add rows."""
    found = await db.execute(
        select(MatchupReport).where(
            MatchupReport.user_id == user_id,
            MatchupReport.champion == champion,
            MatchupReport.opponent == opponent,
            MatchupReport.lane == lane,
        )
    )
    row = found.scalar_one_or_none()
    if row is None:
        row = MatchupReport(user_id=user_id, champion=champion[:40], opponent=opponent[:40], lane=lane[:12])
        db.add(row)
    row.plan_lane = str(plan.get("lane") or "")[:400]
    row.plan_fight = str(plan.get("fight") or "")[:400]
    row.plan_avoid = str(plan.get("avoid") or "")[:400]
    row.source = str(plan.get("source") or "rules")[:20]
    row.updated_at = utc_now()
    await db.commit()


def report_view(row: MatchupReport) -> dict[str, Any]:
    return {
        "id": row.id,
        "champion": row.champion,
        "opponent": row.opponent,
        "lane": row.lane,
        "plan": {"lane": row.plan_lane, "fight": row.plan_fight, "avoid": row.plan_avoid},
        "source": row.source,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_matchup_reports(db: AsyncSession, user_id: int, limit: int = REPORT_LIMIT) -> list[dict[str, Any]]:
    """Newest plans first."""
    found = await db.execute(
        select(MatchupReport)
        .where(MatchupReport.user_id == user_id)
        .order_by(MatchupReport.updated_at.desc())
        .limit(limit)
    )
    return [report_view(row) for row in found.scalars().all()]
