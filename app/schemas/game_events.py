"""
LolAnalyzer Backend - Data Contracts and Schemas
Defines all Pydantic models for Game Events, Player Telemetry, Rule Triggers, AI Coach Advices, and WebSocket messaging.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ==========================================
# Enums
# ==========================================

class Role(str, Enum):
    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MID = "MID"
    ADC = "ADC"
    SUPPORT = "SUPPORT"
    UNKNOWN = "UNKNOWN"


class GameEventType(str, Enum):
    GAME_START = "GAME_START"
    GAME_END = "GAME_END"
    KILL = "KILL"
    DEATH = "DEATH"
    ASSIST = "ASSIST"
    CS_UPDATE = "CS_UPDATE"
    SUMMONER_USED = "SUMMONER_USED"
    OBJECTIVE_KILL = "OBJECTIVE_KILL"
    TURRET_KILL = "TURRET_KILL"
    HEARTBEAT = "HEARTBEAT"


class SummonerSpellType(str, Enum):
    FLASH = "FLASH"
    TELEPORT = "TELEPORT"
    IGNITE = "IGNITE"
    HEAL = "HEAL"
    GHOST = "GHOST"
    BARRIER = "BARRIER"
    EXHAUST = "EXHAUST"
    SMITE = "SMITE"
    CLEANSE = "CLEANSE"
    OTHER = "OTHER"


class TriggerType(str, Enum):
    TILT_RISK = "TILT_RISK"
    CS_CRASH = "CS_CRASH"
    FORCED_FIGHT_NO_SUMMONERS = "FORCED_FIGHT_NO_SUMMONERS"
    OBJECTIVE_CONTEST_RISK = "OBJECTIVE_CONTEST_RISK"
    GENERAL_TACTICAL = "GENERAL_TACTICAL"


class RuleSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class WSMessageType(str, Enum):
    TELEMETRY_INGEST = "TELEMETRY_INGEST"
    GAME_EVENT = "GAME_EVENT"
    RULE_TRIGGERED = "RULE_TRIGGERED"
    TACTICAL_ADVICE = "TACTICAL_ADVICE"
    PING = "PING"
    PONG = "PONG"
    ERROR = "ERROR"
    RESET = "RESET"


# ==========================================
# Telemetry & Game State Models
# ==========================================

class SummonerSpellState(BaseModel):
    spell_name: SummonerSpellType = SummonerSpellType.OTHER
    slot: str = Field(default="D", description="Key slot ('D' or 'F')")
    is_ready: bool = True
    cooldown_remaining_seconds: float = 0.0
    last_used_game_time: Optional[float] = None


class PlayerTelemetry(BaseModel):
    summoner_name: str = Field(default="Summoner")
    champion_name: str = Field(default="UnknownChampion")
    role: Role = Role.UNKNOWN
    level: int = Field(default=1, ge=1, le=18)
    current_gold: int = Field(default=500, ge=0)
    gold_difference: int = Field(default=0, description="Gold difference relative to direct lane matchup")
    kills: int = Field(default=0, ge=0)
    deaths: int = Field(default=0, ge=0)
    assists: int = Field(default=0, ge=0)
    cs: int = Field(default=0, ge=0)
    game_time_seconds: float = Field(default=0.0, ge=0.0)
    summoner_spells: List[SummonerSpellState] = Field(default_factory=list)


class GameEvent(BaseModel):
    event_id: Optional[str] = None
    event_type: GameEventType
    game_time_seconds: float
    raw_data: Dict[str, Any] = Field(default_factory=dict)
    metadata: Optional[Dict[str, Any]] = None


# ==========================================
# Engine Rule Triggers & AI Coach Output
# ==========================================

class RuleTrigger(BaseModel):
    trigger_type: TriggerType
    severity: RuleSeverity
    detected_at_game_time: float
    reason: str
    telemetry_snapshot: PlayerTelemetry
    context_data: Dict[str, Any] = Field(default_factory=dict)


class CoachAdvice(BaseModel):
    advice_id: str
    trigger_type: TriggerType
    severity: RuleSeverity
    text: str = Field(description="Imperative tactical advice, maximum 12 words")
    role: Role
    champion_name: str
    game_time_seconds: float
    generated_by: str = Field(default="heuristic_engine", description="'gemini-1.5-flash', 'heuristic_engine', etc.")


# ==========================================
# WebSocket Message Protocol
# ==========================================

class WSMessage(BaseModel):
    type: WSMessageType
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=float)


# ==========================================
# Champ Select LCU Models
# ==========================================

class ChampSelectAction(BaseModel):
    actor_cell_id: int
    champion_id: int
    type: str  # "pick" or "ban"
    is_in_progress: bool
    completed: bool


class ChampSelectSession(BaseModel):
    is_active: bool = False
    game_id: Optional[int] = None
    my_team_picks: List[int] = Field(default_factory=list)
    their_team_picks: List[int] = Field(default_factory=list)
    bans: List[int] = Field(default_factory=list)
    assigned_role: Role = Role.UNKNOWN
    assigned_champion_id: Optional[int] = None


class PickRecommendation(BaseModel):
    champion_id: int
    champion_name: str
    role: Role
    synergy_score: float = Field(ge=0.0, le=100.0)
    counter_score: float = Field(ge=0.0, le=100.0)
    reasoning: str
