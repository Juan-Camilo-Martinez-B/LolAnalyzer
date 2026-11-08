"""
Data read from the League client on the player's machine (full and local modes).

In local mode the agent cannot store anything: it reads the client and sends
the result to the cloud with the player's JWT.
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.riot import _local_ranked_stats, _local_summoner, _solo_from_lcu
from app.core.access import bearer_token, require_player
from app.core.config import settings
from app.db.models import User
from app.db.session import get_db
from app.services.cloud_bridge import CloudUnavailable, cloud_post
from app.services.local_match_history import read_lcu_history, sync_lcu_history

router = APIRouter(prefix="/api/local", tags=["Local client"])

SYNC_LIMIT = 10


@router.post("/history/sync")
async def sync_history(
    player: Optional[User] = Depends(require_player),
    db: AsyncSession = Depends(get_db),
    forward_token: Optional[str] = Depends(bearer_token),
) -> dict[str, Any]:
    """
    Copies the open client's profile and recent Summoner's Rift games to the
    account, whatever League account is open. Returns that profile's puuid so
    the desktop can follow it.
    """
    if player is not None:
        history = await sync_lcu_history(db, player, limit=SYNC_LIMIT)
    else:
        history = await read_lcu_history(SYNC_LIMIT)
        if history is not None and history.puuid:
            try:
                await cloud_post("/api/history/import", forward_token, {
                    "puuid": history.puuid,
                    "profile": history.profile,
                    "matches": history.matches,
                })
            except CloudUnavailable as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if history is None:
        return {"clientConnected": False, "synced": 0, "puuid": None}
    return {"clientConnected": True, "synced": len(history.matches), "puuid": history.puuid or None}


@router.get("/profile")
async def client_profile(_player: Optional[User] = Depends(require_player)) -> dict[str, Any]:
    """Level and rank of the account open in the client, without the Riot API key."""
    summoner = await _local_summoner()
    if not summoner:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Abre el cliente de League.")
    return {
        "puuid": summoner.get("puuid"),
        "gameName": summoner.get("gameName"),
        "tagLine": summoner.get("tagLine"),
        "summonerLevel": summoner.get("summonerLevel"),
        "profileIconId": summoner.get("profileIconId"),
        "rankedSolo": _solo_from_lcu(await _local_ranked_stats()),
        "source": "client",
        "mode": settings.mode,
    }
