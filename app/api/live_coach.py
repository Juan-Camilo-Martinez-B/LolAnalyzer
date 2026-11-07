"""Live match coach and the advice history stored on the account."""

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import bearer_token, require_player
from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.coach_metrics import load_metrics, record_advice
from app.services.live_coach import live_coach

# Reads the game on the player's machine (full and local modes).
live_router = APIRouter(prefix="/api/coach", tags=["Coach"])
# Account data (full and cloud modes).
history_router = APIRouter(prefix="/api/coach", tags=["Coach"])

# Kept for code that imports the old single router.
router = live_router


class AdviceLogRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=240)
    severity: str = Field(default="INFO", max_length=20)
    trigger: str = Field(default="GENERAL_TACTICAL", max_length=40)
    category: Optional[str] = Field(default=None, max_length=20)
    champion: str = Field(default="Unknown", max_length=40)
    role: str = Field(default="UNKNOWN", max_length=20)
    gameTime: float = Field(default=0, ge=0, le=36000)
    source: str = Field(default="rules", max_length=20)


@live_router.get("/live")
async def live_advice(
    player: Optional[User] = Depends(require_player),
    db: AsyncSession = Depends(get_db),
    forward_token: Optional[str] = Depends(bearer_token),
) -> dict[str, Any]:
    if player is None:
        # Local agent: no database; new tips are forwarded to the cloud with the player's token.
        return await live_coach.snapshot(None, None, forward_token)
    return await live_coach.snapshot(db, player.id)


@history_router.get("/metrics")
async def coach_metrics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await load_metrics(db, current_user.id)


@history_router.post("/advice")
async def log_advice(
    body: AdviceLogRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Stores a tip the local agent produced during a match."""
    await record_advice(db, current_user.id, body.model_dump())
    return {"stored": True}
