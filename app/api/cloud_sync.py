"""
Cloud endpoints the local agent calls with the player's JWT (full and cloud modes).

The agent reads the League client but holds no database credentials or API
keys, so it sends history here and asks here for the AI lane plan.
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.local_match_history import ClientHistory, save_local_matches, store_client_history
from app.services.matchup_plan import matchup_plan

router = APIRouter(tags=["Cloud sync"])


class ImportedMatch(BaseModel):
    matchId: str = Field(..., min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")
    queueId: int = Field(default=0, ge=0, le=10000)
    gameMode: str = Field(default="CLASSIC", max_length=40)
    isWin: bool = False
    championName: str = Field(default="Unknown", max_length=100)
    role: str = Field(default="UNKNOWN", max_length=20)
    kills: int = Field(default=0, ge=0, le=200)
    deaths: int = Field(default=0, ge=0, le=200)
    assists: int = Field(default=0, ge=0, le=200)
    kda: float = Field(default=0, ge=0, le=500)
    cs: int = Field(default=0, ge=0, le=5000)
    csPerMin: float = Field(default=0, ge=0, le=100)
    durationSec: int = Field(default=0, ge=0, le=36000)
    timestamp: Optional[str] = Field(default=None, max_length=40)
    items: list[int] = Field(default_factory=list, max_length=7)
    trinketId: int = Field(default=0, ge=0, le=100000)
    goldEarned: int = Field(default=0, ge=0, le=500000)
    visionScore: int = Field(default=0, ge=0, le=1000)
    damageDealt: int = Field(default=0, ge=0, le=2000000)


class RankedSnapshot(BaseModel):
    tier: Optional[str] = Field(default=None, max_length=20)
    rank: Optional[str] = Field(default=None, max_length=5)
    leaguePoints: int = Field(default=0, ge=0, le=10000)
    wins: int = Field(default=0, ge=0, le=100000)
    losses: int = Field(default=0, ge=0, le=100000)
    queueType: Optional[str] = Field(default=None, max_length=30)


class ProfileSnapshot(BaseModel):
    gameName: Optional[str] = Field(default=None, max_length=100)
    tagLine: Optional[str] = Field(default=None, max_length=20)
    profileIconId: Optional[int] = Field(default=None, ge=0, le=1000000)
    summonerLevel: Optional[int] = Field(default=None, ge=0, le=100000)
    rankedSolo: Optional[RankedSnapshot] = None


class HistoryImportRequest(BaseModel):
    puuid: Optional[str] = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_\-]+$")
    profile: Optional[ProfileSnapshot] = None
    matches: list[ImportedMatch] = Field(..., max_length=20)


class PlanFacts(BaseModel):
    laneAdvantage: Optional[str] = Field(default=None, max_length=40)
    soloKillAdvantage: Optional[str] = Field(default=None, max_length=40)
    playStyle: Optional[str] = Field(default=None, max_length=20)
    skillOrder: list[str] = Field(default_factory=list, max_length=6)
    tip: Optional[str] = Field(default=None, max_length=600)


class MatchupPlanRequest(BaseModel):
    champion: str = Field(..., min_length=1, max_length=40)
    opponent: str = Field(..., min_length=1, max_length=40)
    lane: str = Field(..., pattern=r"^(top|jungle|mid|adc|support)$")
    facts: PlanFacts = Field(default_factory=PlanFacts)


@router.post("/api/history/import")
async def import_history(
    body: HistoryImportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Stores games the local agent read from the client, under the League
    profile they were played with. Any League account can feed any analyzer
    account: the dashboard keeps each profile apart.
    """
    matches = [match.model_dump() for match in body.matches]
    if body.puuid:
        snapshot = body.profile.model_dump(exclude_none=True) if body.profile else {}
        profile = {**snapshot, "puuid": body.puuid}
        await store_client_history(db, current_user.id, ClientHistory(body.puuid, matches, profile))
    else:
        await save_local_matches(db, current_user.id, matches, source="lcu")
    return {"stored": len(body.matches)}


@router.post("/api/ai/matchup-plan")
async def ai_matchup_plan(
    body: MatchupPlanRequest,
    _current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Lane plan with Groq for the local agent, cached per matchup."""
    facts = body.facts.model_dump()
    return await matchup_plan(body.champion, body.opponent, body.lane, facts)
