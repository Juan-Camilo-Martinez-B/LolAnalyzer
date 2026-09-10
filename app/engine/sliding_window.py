"""
LolAnalyzer Backend - Sliding Window Engine
Maintains in-memory time-series buffers of player telemetry and game events
for sub-millisecond heuristic pattern evaluation.
"""

from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Tuple

from app.core.config import settings
from app.schemas.game_events import (
    GameEvent,
    GameEventType,
    PlayerTelemetry,
    SummonerSpellState,
    SummonerSpellType,
)


@dataclass
class CSDataPoint:
    game_time: float
    cs: int


@dataclass
class SummonerUsageRecord:
    spell_name: SummonerSpellType
    slot: str
    used_at_game_time: float
    cooldown_seconds: float


class SlidingWindowBuffer:
    """
    Time-series circular buffer managing rolling telemetry and in-game events
    over a configurable time window (default 180 seconds / 3 minutes).
    """

    def __init__(self, window_seconds: Optional[int] = None):
        self.window_seconds: float = float(window_seconds or settings.SLIDING_WINDOW_SECONDS)
        
        # Event deques
        self._events: Deque[GameEvent] = deque()
        self._death_events: Deque[GameEvent] = deque()
        self._kill_events: Deque[GameEvent] = deque()
        self._assist_events: Deque[GameEvent] = deque()
        
        # CS and Telemetry tracking
        self._cs_history: Deque[CSDataPoint] = deque()
        self._last_cs_change_time: Optional[float] = None
        self._last_cs_value: int = 0
        
        # Summoner tracking
        self._summoner_states: dict[SummonerSpellType, SummonerSpellState] = {}
        self._summoner_usages: Deque[SummonerUsageRecord] = deque()
        
        # Current active telemetry snapshot
        self.latest_telemetry: Optional[PlayerTelemetry] = None

    def purge_expired(self, current_game_time: float) -> None:
        """Removes records older than current_game_time - window_seconds."""
        threshold = max(0.0, current_game_time - self.window_seconds)

        while self._events and self._events[0].game_time_seconds < threshold:
            self._events.popleft()

        while self._death_events and self._death_events[0].game_time_seconds < threshold:
            self._death_events.popleft()

        while self._kill_events and self._kill_events[0].game_time_seconds < threshold:
            self._kill_events.popleft()

        while self._assist_events and self._assist_events[0].game_time_seconds < threshold:
            self._assist_events.popleft()

        while self._cs_history and self._cs_history[0].game_time < threshold:
            self._cs_history.popleft()

        while self._summoner_usages and (
            self._summoner_usages[0].used_at_game_time + self._summoner_usages[0].cooldown_seconds < threshold
        ):
            self._summoner_usages.popleft()

    def add_event(self, event: GameEvent) -> None:
        """Appends a new discrete game event to the sliding window buffer."""
        self._events.append(event)
        
        if event.event_type == GameEventType.DEATH:
            self._death_events.append(event)
        elif event.event_type == GameEventType.KILL:
            self._kill_events.append(event)
        elif event.event_type == GameEventType.ASSIST:
            self._assist_events.append(event)
        elif event.event_type == GameEventType.SUMMONER_USED:
            spell_name = event.raw_data.get("spell_name")
            slot = event.raw_data.get("slot", "D")
            cooldown = float(event.raw_data.get("cooldown_seconds", 300.0))
            if spell_name:
                try:
                    spell_type = SummonerSpellType(spell_name)
                    self.record_summoner_usage(
                        spell_type=spell_type,
                        slot=slot,
                        used_at_game_time=event.game_time_seconds,
                        cooldown_seconds=cooldown,
                    )
                except ValueError:
                    pass

        self.purge_expired(event.game_time_seconds)

    def update_telemetry(self, telemetry: PlayerTelemetry) -> None:
        """Updates the latest telemetry snapshot and records CS / summoner time-series."""
        self.latest_telemetry = telemetry
        current_time = telemetry.game_time_seconds

        # CS progression tracking
        if telemetry.cs != self._last_cs_value:
            self._last_cs_change_time = current_time
            self._last_cs_value = telemetry.cs
        elif self._last_cs_change_time is None:
            self._last_cs_change_time = current_time

        self._cs_history.append(CSDataPoint(game_time=current_time, cs=telemetry.cs))

        # Summoner spells states update
        for spell in telemetry.summoner_spells:
            self._summoner_states[spell.spell_name] = spell

        self.purge_expired(current_time)

    def record_summoner_usage(
        self,
        spell_type: SummonerSpellType,
        slot: str,
        used_at_game_time: float,
        cooldown_seconds: float = 300.0
    ) -> None:
        """Explicitly records summoner spell usage and sets cooldown state."""
        self._summoner_usages.append(
            SummonerUsageRecord(
                spell_name=spell_type,
                slot=slot,
                used_at_game_time=used_at_game_time,
                cooldown_seconds=cooldown_seconds
            )
        )
        self._summoner_states[spell_type] = SummonerSpellState(
            spell_name=spell_type,
            slot=slot,
            is_ready=False,
            cooldown_remaining_seconds=cooldown_seconds,
            last_used_game_time=used_at_game_time
        )

    def get_recent_deaths_count(self, current_game_time: float, window_seconds: Optional[float] = None) -> int:
        """Returns the number of deaths within the specified time window (default: buffer window)."""
        duration = window_seconds if window_seconds is not None else self.window_seconds
        threshold = max(0.0, current_game_time - duration)
        return sum(1 for d in self._death_events if d.game_time_seconds >= threshold)

    def get_recent_kills_and_assists_count(
        self, current_game_time: float, window_seconds: Optional[float] = None
    ) -> int:
        """Returns total kills and assists within the specified time window."""
        duration = window_seconds if window_seconds is not None else self.window_seconds
        threshold = max(0.0, current_game_time - duration)
        kills = sum(1 for k in self._kill_events if k.game_time_seconds >= threshold)
        assists = sum(1 for a in self._assist_events if a.game_time_seconds >= threshold)
        return kills + assists

    def get_recent_cs_rate(self, current_game_time: float, window_seconds: float = 120.0) -> float:
        """
        Calculates CS per minute rate within the specified trailing window.
        Returns 0.0 if not enough data points exist.
        """
        threshold = max(0.0, current_game_time - window_seconds)
        relevant_points = [p for p in self._cs_history if p.game_time >= threshold]

        if len(relevant_points) < 2:
            return 0.0

        oldest = relevant_points[0]
        latest = relevant_points[-1]
        time_diff = latest.game_time - oldest.game_time

        if time_diff <= 0:
            return 0.0

        cs_diff = latest.cs - oldest.cs
        minutes = time_diff / 60.0
        return max(0.0, cs_diff / minutes)

    def get_time_since_last_cs(self, current_game_time: float) -> float:
        """Returns elapsed seconds since player's CS last incremented."""
        if self._last_cs_change_time is None:
            return 0.0
        return max(0.0, current_game_time - self._last_cs_change_time)

    def is_summoner_ready(self, spell_type: SummonerSpellType, current_game_time: float) -> bool:
        """
        Checks whether a summoner spell (e.g., FLASH) is available.
        Evaluates both recorded usage cooldowns and telemetry state.
        """
        for usage in reversed(self._summoner_usages):
            if usage.spell_name == spell_type:
                elapsed = current_game_time - usage.used_at_game_time
                if elapsed < usage.cooldown_seconds:
                    return False
                return True

        state = self._summoner_states.get(spell_type)
        if state is not None:
            if not state.is_ready and state.last_used_game_time is not None:
                elapsed = current_game_time - state.last_used_game_time
                if elapsed < state.cooldown_remaining_seconds:
                    return False
                return True
            return state.is_ready

        return True

    def get_summoner_cooldown_remaining(
        self, spell_type: SummonerSpellType, current_game_time: float
    ) -> float:
        """Returns remaining cooldown in seconds for a summoner spell."""
        for usage in reversed(self._summoner_usages):
            if usage.spell_name == spell_type:
                elapsed = current_game_time - usage.used_at_game_time
                remaining = usage.cooldown_seconds - elapsed
                return max(0.0, remaining)

        state = self._summoner_states.get(spell_type)
        if state is not None and not state.is_ready:
            if state.last_used_game_time is not None:
                elapsed = current_game_time - state.last_used_game_time
                remaining = state.cooldown_remaining_seconds - elapsed
                return max(0.0, remaining)
            return max(0.0, state.cooldown_remaining_seconds)

        return 0.0

    def reset(self) -> None:
        """Resets all sliding window data and buffers."""
        self._events.clear()
        self._death_events.clear()
        self._kill_events.clear()
        self._assist_events.clear()
        self._cs_history.clear()
        self._last_cs_change_time = None
        self._last_cs_value = 0
        self._summoner_states.clear()
        self._summoner_usages.clear()
        self.latest_telemetry = None
