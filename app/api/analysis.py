"""Post-match analysis, written once per game from the final match summary."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.local_match_history import sync_lcu_history
from app.services.match_analysis import (
    analysis_view,
    analyze_match,
    latest_finished_match,
    owned_match,
    stored_analyses,
    stored_analysis,
)

logger = logging.getLogger("lol_analyzer.analysis_routes")

router = APIRouter(prefix="/api/analysis", tags=["Post-match analysis"])

MatchId = Path(..., min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")


@router.post("/latest")
async def analyze_latest(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Called by the desktop when a game ends. Syncs the client's history, then
    analyses the newest finished game once. Returns analysis None when there is
    no recent game yet (the client may still be writing it).
    """
    try:
        await sync_lcu_history(db, current_user, limit=5)
    except Exception as exc:
        logger.info("History sync before analysis failed: %s", type(exc).__name__)
    match = await latest_finished_match(db, current_user.id)
    if match is None:
        return {"analysis": None}
    row = await analyze_match(db, current_user.id, match)
    return {"analysis": analysis_view(row)}


@router.get("")
async def list_analyses(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Every post-match report of the account, newest first."""
    return [analysis_view(row) for row in await stored_analyses(db, current_user.id)]


@router.get("/{match_id}")
async def get_analysis(
    match_id: str = MatchId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await stored_analysis(db, current_user.id, match_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Esta partida todavía no tiene análisis.")
    return analysis_view(row)


@router.post("/{match_id}")
async def create_analysis(
    match_id: str = MatchId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Analyses an older game on demand. Repeating it returns the stored analysis."""
    match = await owned_match(db, current_user.id, match_id)
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontramos esa partida en tu historial.")
    row = await analyze_match(db, current_user.id, match)
    return analysis_view(row)
