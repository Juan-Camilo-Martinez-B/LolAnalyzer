"""
LolAnalyzer Backend - Visual Analytics & Charting Schemas
Defines data structures tailored for frontend visualization charts
(Line chart for CS progression, Bar/Heatmap for Tilt time-intervals,
Radar chart for 5-axis tactical skill evaluation, and Donut chart for AI Coach impact).
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class CSProgressionPoint(BaseModel):
    timestamp_minute: float = Field(..., description="Timestamp in game minutes (e.g. 5.0, 10.0)")
    actual_cs: int = Field(..., ge=0)
    actual_cs_per_min: float = Field(..., ge=0.0)
    challenger_target_cs: int = Field(..., ge=0, description="Benchmark Challenger CS target")
    has_cs_crash: bool = Field(default=False, description="True if CS/min dropped below acceptable threshold")
    advice_text: Optional[str] = None


class CSProgressionChartData(BaseModel):
    match_id: int
    champion_name: str
    role: str
    duration_minutes: float
    total_cs: int
    final_cs_per_minute: float
    points: List[CSProgressionPoint]


class TiltHeatmapBucket(BaseModel):
    time_bucket: str = Field(..., description="Interval label e.g. '0-5m', '5-10m', '10-15m'")
    deaths_count: int = Field(default=0, ge=0)
    tilt_triggers_count: int = Field(default=0, ge=0)
    deaths_percentage: float = Field(default=0.0, ge=0.0, le=100.0)


class TiltHeatmapChartData(BaseModel):
    total_matches_analyzed: int
    total_deaths: int
    total_tilt_triggers: int
    peak_danger_interval: str
    buckets: List[TiltHeatmapBucket]


class RadarAxisScore(BaseModel):
    axis: str = Field(..., description="Farming, Combat, Vision, Objectives, Survivability")
    score: float = Field(..., ge=0.0, le=100.0, description="Normalized score 0-100")
    benchmark_score: float = Field(default=80.0, description="Challenger benchmark score")


class RoleRadarChartData(BaseModel):
    role: str
    matches_evaluated: int
    overall_performance_rating: str  # e.g. "S+", "A", "B", "C"
    scores: List[RadarAxisScore]


class CoachImpactSlice(BaseModel):
    category: str = Field(..., description="'Followed & Won', 'Followed & Lost', 'Ignored & Won', 'Ignored & Lost'")
    count: int = Field(default=0, ge=0)
    percentage: float = Field(default=0.0, ge=0.0, le=100.0)


class CoachImpactChartData(BaseModel):
    total_advices_received: int
    total_advices_followed: int
    compliance_rate_percentage: float
    winrate_when_followed: float
    winrate_when_ignored: float
    coach_efficacy_delta: float = Field(..., description="Difference in winrate when following advice (+/- %)")
    slices: List[CoachImpactSlice]
