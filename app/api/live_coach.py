"""Live match coach. The desktop polls this while League is open."""

from typing import Any

from fastapi import APIRouter

from app.services.live_coach import live_coach

router = APIRouter(prefix="/api/coach", tags=["Coach"])


@router.get("/live")
async def live_advice() -> dict[str, Any]:
    return await live_coach.snapshot()
