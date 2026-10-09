"""Live match coach and the advice history stored on the account."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.coach_metrics import load_metrics
from app.services.live_coach import live_coach

router = APIRouter(prefix="/api/coach", tags=["Coach"])


@router.get("/live")
async def live_advice(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await live_coach.snapshot(db, current_user.id)


@router.get("/metrics")
async def coach_metrics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await load_metrics(db, current_user.id)
