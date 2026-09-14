"""
LolAnalyzer Backend - Match Statistics Schemas
Validation and serialization models for Match records, Telemetry Time-Series,
Paginated match history, and aggregated Performance Summaries.
"""

from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TelemetryPointCreate(BaseModel):
    game_time_seconds: float = Field(..., ge=0, description="In-game timestamp in seconds")
    cs: int = Field(default=0, ge=0)
    cs_per_minute: float = Field(default=0.0, ge=0.0)
    kills: int = Field(default=0, ge=0)
    deaths: int = Field(default=0, ge=0)
    flash_ready: bool = Field(default=True)
    advice_text: Optional[str] = None


class TelemetryPointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match_id: int
    game_time_seconds: float
    cs: int
    cs_per_minute: float
    kills: int
    deaths: int
    flash_ready: bool
    advice_text: Optional[str] = None


class MatchCreate(BaseModel):
    game_id: Optional[int] = Field(default=None, description="Riot game ID if available")
    champion_name: str = Field(..., min_length=1, max_length=100)
    role: str = Field(..., description="TOP, JUNGLE, MID, ADC, SUPPORT")
    kills: int = Field(default=0, ge=0)
    deaths: int = Field(default=0, ge=0)
    assists: int = Field(default=0, ge=0)
    cs: int = Field(default=0, ge=0)
    gold_earned: int = Field(default=0, ge=0)
    gold_difference: int = Field(default=0)
    duration_seconds: int = Field(..., ge=0, description="Total match duration in seconds")
    win: bool = Field(default=False)
    tilt_triggers_count: int = Field(default=0, ge=0)
    advices_received_count: int = Field(default=0, ge=0)
    advices_followed_count: int = Field(default=0, ge=0)
    telemetry_points: List[TelemetryPointCreate] = Field(default_factory=list)


class MatchListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_id: Optional[int] = None
    champion_name: str
    role: str
    kills: int
    deaths: int
    assists: int
    kda_ratio: float
    cs: int
    cs_per_minute: float
    gold_earned: int
    gold_difference: int
    duration_seconds: int
    win: bool
    tilt_triggers_count: int
    advices_received_count: int
    advices_followed_count: int
    played_at: datetime


class MatchDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    game_id: Optional[int] = None
    champion_name: str
    role: str
    kills: int
    deaths: int
    assists: int
    kda_ratio: float
    cs: int
    cs_per_minute: float
    gold_earned: int
    gold_difference: int
    duration_seconds: int
    win: bool
    tilt_triggers_count: int
    advices_received_count: int
    advices_followed_count: int
    played_at: datetime
    telemetry_points: List[TelemetryPointResponse] = Field(default_factory=list)


class PaginatedMatchesResponse(BaseModel):
    total_count: int
    limit: int
    skip: int
    matches: List[MatchListItem]


class RoleStatSummary(BaseModel):
    games: int
    wins: int
    losses: int
    winrate_percentage: float


class ChampionStatSummary(BaseModel):
    champion_name: str
    games: int
    wins: int
    losses: int
    winrate_percentage: float
    avg_kda: float


class StatsSummary(BaseModel):
    total_matches: int
    total_wins: int
    total_losses: int
    overall_winrate_percentage: float
    total_kills: int
    total_deaths: int
    total_assists: int
    average_kda: float
    average_cs: float
    average_cs_per_minute: float
    total_duration_minutes: float
    total_tilt_triggers: int
    avg_tilt_triggers_per_match: float
    total_coach_advices: int
    total_coach_advices_followed: int
    coach_compliance_rate_percentage: float
    role_stats: Dict[str, RoleStatSummary]
    top_champions: List[ChampionStatSummary]


class ResetStatsResponse(BaseModel):
    status: str = "stats_reset"
    message: str
    deleted_matches_count: int
