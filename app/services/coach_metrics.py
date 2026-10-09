"""Stored coach tips and the totals the desktop shows for an account."""

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CoachAdviceLog


def summarize_advices(rows: list[CoachAdviceLog]) -> dict[str, Any]:
    counts = Counter(row.trigger_type for row in rows)
    recent = rows[:12]
    return {
        "total": len(rows),
        "fromGemini": sum(1 for row in rows if row.source == "gemini"),
        "fromHeuristic": sum(1 for row in rows if row.source != "gemini"),
        "byTrigger": [
            {"trigger": trigger, "count": count}
            for trigger, count in counts.most_common()
        ],
        "recent": [
            {
                "id": row.id,
                "text": row.advice_text,
                "trigger": row.trigger_type,
                "severity": row.severity,
                "champion": row.champion_name,
                "role": row.role,
                "gameTime": row.game_time_seconds,
                "source": row.source,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
            }
            for row in recent
        ],
    }


async def record_advice(db: AsyncSession, user_id: int, advice: dict[str, Any]) -> None:
    db.add(CoachAdviceLog(
        user_id=user_id,
        advice_text=str(advice.get("text") or "")[:240],
        trigger_type=str(advice.get("trigger") or "GENERAL_TACTICAL")[:40],
        severity=str(advice.get("severity") or "INFO")[:20],
        champion_name=str(advice.get("champion") or "Unknown")[:40],
        role=str(advice.get("role") or "UNKNOWN")[:20],
        game_time_seconds=float(advice.get("gameTime") or 0),
        source="gemini" if advice.get("source") == "gemini" else "heuristic",
    ))
    await db.flush()


async def load_metrics(db: AsyncSession, user_id: int) -> dict[str, Any]:
    result = await db.execute(
        select(CoachAdviceLog)
        .where(CoachAdviceLog.user_id == user_id)
        .order_by(CoachAdviceLog.created_at.desc())
    )
    return summarize_advices(list(result.scalars().all()))
