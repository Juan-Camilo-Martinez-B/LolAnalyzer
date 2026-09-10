"""
LolAnalyzer Backend - Tactical Heuristic Rules Evaluator
Evaluates sliding-window telemetry to detect critical tactical deviation patterns:
- Tilt Risk: Fast consecutive deaths or death streaks without kill participation.
- CS Crash: Drastic collapse in farming efficiency during laning phase.
- Forced Fights Without Summoners: Engaging in hazardous trades or dying while Flash is on cooldown.
"""

from typing import Dict, List, Optional
from app.core.config import settings
from app.engine.sliding_window import SlidingWindowBuffer
from app.schemas.game_events import (
    GameEvent,
    GameEventType,
    PlayerTelemetry,
    Role,
    RuleSeverity,
    RuleTrigger,
    SummonerSpellType,
    TriggerType,
)


class RulesEvaluator:
    """
    High-performance, deterministic rule evaluation engine.
    Executes in <1ms to detect behavioral anti-patterns and triggers structured alerts.
    """

    def __init__(
        self,
        tilt_death_threshold: Optional[int] = None,
        cs_crash_threshold: Optional[float] = None,
        advice_cooldown_seconds: Optional[int] = None,
    ):
        self.tilt_death_threshold: int = (
            tilt_death_threshold or settings.TILT_DEATH_THRESHOLD
        )
        self.cs_crash_threshold: float = (
            cs_crash_threshold or settings.CS_CRASH_THRESHOLD
        )
        self.advice_cooldown_seconds: float = float(
            advice_cooldown_seconds or settings.COOLDOWN_BETWEEN_ADVICE_SECONDS
        )

        # Internal cooldown tracking per trigger type
        self._trigger_cooldowns: Dict[TriggerType, float] = {}
        self._last_global_trigger_time: float = -9999.0

        # Dedicated rule-specific cooldowns (in seconds)
        self._cooldown_durations: Dict[TriggerType, float] = {
            TriggerType.TILT_RISK: 90.0,
            TriggerType.CS_CRASH: 120.0,
            TriggerType.FORCED_FIGHT_NO_SUMMONERS: 60.0,
            TriggerType.OBJECTIVE_CONTEST_RISK: 60.0,
            TriggerType.GENERAL_TACTICAL: 45.0,
        }

    def _is_on_cooldown(self, trigger_type: TriggerType, current_game_time: float) -> bool:
        """Checks if a specific trigger type or the global advice rate is in cooldown."""
        # Global throttle check
        if (current_game_time - self._last_global_trigger_time) < self.advice_cooldown_seconds:
            return True

        # Rule-specific throttle check
        last_time = self._trigger_cooldowns.get(trigger_type, -9999.0)
        cooldown_duration = self._cooldown_durations.get(trigger_type, 30.0)
        return (current_game_time - last_time) < cooldown_duration

    def _record_trigger(self, trigger_type: TriggerType, current_game_time: float) -> None:
        """Registers a fired trigger timestamp to enforce cooldowns."""
        self._trigger_cooldowns[trigger_type] = current_game_time
        self._last_global_trigger_time = current_game_time

    def evaluate(
        self,
        buffer: SlidingWindowBuffer,
        telemetry: Optional[PlayerTelemetry] = None,
    ) -> Optional[RuleTrigger]:
        """
        Main evaluation entry point. Analyzes rolling metrics against active rules in priority order:
        1. Tilt Risk (Highest priority)
        2. Forced Fights without Summoners
        3. CS Crash / Stalled Economy
        """
        current_telemetry = telemetry or buffer.latest_telemetry
        if not current_telemetry:
            return None

        current_time = current_telemetry.game_time_seconds

        # 1. Evaluate Tilt Risk
        tilt_trigger = self._check_tilt_risk(buffer, current_telemetry, current_time)
        if tilt_trigger:
            return tilt_trigger

        # 2. Evaluate Forced Fight without Flash/Summoners
        summoner_trigger = self._check_forced_fight_no_summoners(
            buffer, current_telemetry, current_time
        )
        if summoner_trigger:
            return summoner_trigger

        # 3. Evaluate CS Crash (Only relevant after 3:00 and not Support)
        cs_trigger = self._check_cs_crash(buffer, current_telemetry, current_time)
        if cs_trigger:
            return cs_trigger

        return None

    def evaluate_event(
        self,
        buffer: SlidingWindowBuffer,
        event: GameEvent,
        telemetry: Optional[PlayerTelemetry] = None,
    ) -> Optional[RuleTrigger]:
        """
        Evaluates discrete, high-impact game events (e.g. immediate death event).
        """
        buffer.add_event(event)
        current_telemetry = telemetry or buffer.latest_telemetry
        if not current_telemetry:
            return None

        # On death event, immediately check for Tilt Risk or Summoner absence
        if event.event_type == GameEventType.DEATH:
            return self.evaluate(buffer, current_telemetry)

        return None

    # ==========================================
    # Individual Rule Heuristics
    # ==========================================

    def _check_tilt_risk(
        self,
        buffer: SlidingWindowBuffer,
        telemetry: PlayerTelemetry,
        current_time: float,
    ) -> Optional[RuleTrigger]:
        """
        Triggers if player suffered >=2 deaths in the 3-minute window or has accumulated
        multiple deaths without any kill/assist contribution.
        """
        if self._is_on_cooldown(TriggerType.TILT_RISK, current_time):
            return None

        recent_deaths = buffer.get_recent_deaths_count(current_time)
        recent_k_a = buffer.get_recent_kills_and_assists_count(current_time)

        # Condition A: >= 2 deaths in the sliding window
        # Condition B: >= 3 total deaths with 0 kills and 0 assists
        is_rapid_deaths = recent_deaths >= self.tilt_death_threshold
        is_isolated_feeding = telemetry.deaths >= 3 and (telemetry.kills + telemetry.assists) == 0

        if is_rapid_deaths or is_isolated_feeding:
            severity = RuleSeverity.CRITICAL if recent_deaths >= 3 else RuleSeverity.WARNING
            reason = (
                f"Riesgo de tilt detectado: {recent_deaths} muertes en los últimos "
                f"{int(buffer.window_seconds / 60)} min (KDA: {telemetry.kills}/{telemetry.deaths}/{telemetry.assists})"
            )

            self._record_trigger(TriggerType.TILT_RISK, current_time)
            return RuleTrigger(
                trigger_type=TriggerType.TILT_RISK,
                severity=severity,
                detected_at_game_time=current_time,
                reason=reason,
                telemetry_snapshot=telemetry,
                context_data={
                    "recent_deaths": recent_deaths,
                    "recent_kills_assists": recent_k_a,
                    "total_deaths": telemetry.deaths,
                    "window_seconds": buffer.window_seconds,
                },
            )

        return None

    def _check_forced_fight_no_summoners(
        self,
        buffer: SlidingWindowBuffer,
        telemetry: PlayerTelemetry,
        current_time: float,
    ) -> Optional[RuleTrigger]:
        """
        Triggers when player dies or engages while key defensive summoner (Flash) is on cooldown.
        """
        if self._is_on_cooldown(TriggerType.FORCED_FIGHT_NO_SUMMONERS, current_time):
            return None

        # Check if Flash is currently on cooldown with substantial remaining time
        is_flash_ready = buffer.is_summoner_ready(SummonerSpellType.FLASH, current_time)
        flash_cd_remaining = buffer.get_summoner_cooldown_remaining(
            SummonerSpellType.FLASH, current_time
        )

        recent_deaths = buffer.get_recent_deaths_count(current_time, window_seconds=60.0)

        # Trigger if player died in the last minute while Flash was down (>20s cooldown left)
        if not is_flash_ready and flash_cd_remaining > 20.0 and recent_deaths >= 1:
            reason = (
                f"Pelea forzada sin Destello: Muerte ocurrida con Flash en enfriamiento "
                f"({int(flash_cd_remaining)}s restantes)."
            )

            self._record_trigger(TriggerType.FORCED_FIGHT_NO_SUMMONERS, current_time)
            return RuleTrigger(
                trigger_type=TriggerType.FORCED_FIGHT_NO_SUMMONERS,
                severity=RuleSeverity.WARNING,
                detected_at_game_time=current_time,
                reason=reason,
                telemetry_snapshot=telemetry,
                context_data={
                    "flash_cooldown_remaining": flash_cd_remaining,
                    "recent_deaths": recent_deaths,
                },
            )

        return None

    def _check_cs_crash(
        self,
        buffer: SlidingWindowBuffer,
        telemetry: PlayerTelemetry,
        current_time: float,
    ) -> Optional[RuleTrigger]:
        """
        Triggers when a farming lane (Top, Mid, ADC, Jungle) suffers a sharp drop in CS rate
        or has stopped farming for >90 seconds during laning phase.
        """
        # Supports are exempt from CS crash evaluation
        if telemetry.role == Role.SUPPORT:
            return None

        # Only evaluate after minion waves crash in lane (3:00 = 180s) and before late game (25:00 = 1500s)
        if current_time < 180.0 or current_time > 1500.0:
            return None

        if self._is_on_cooldown(TriggerType.CS_CRASH, current_time):
            return None

        recent_cs_rate = buffer.get_recent_cs_rate(current_time, window_seconds=120.0)
        time_without_cs = buffer.get_time_since_last_cs(current_time)

        # Condition 1: Trailing CS rate below threshold (< 4.0 CS/min)
        # Condition 2: No CS gained in the last 90 seconds
        is_cs_rate_low = recent_cs_rate < self.cs_crash_threshold and recent_cs_rate > 0.0
        is_farming_halted = time_without_cs >= 90.0

        if is_cs_rate_low or is_farming_halted:
            reason = (
                f"Colapso de farmeo detectado: {recent_cs_rate:.1f} CS/min en últimos 2 min "
                f"({int(time_without_cs)}s sin farmear)."
            )

            self._record_trigger(TriggerType.CS_CRASH, current_time)
            return RuleTrigger(
                trigger_type=TriggerType.CS_CRASH,
                severity=RuleSeverity.WARNING,
                detected_at_game_time=current_time,
                reason=reason,
                telemetry_snapshot=telemetry,
                context_data={
                    "recent_cs_rate": round(recent_cs_rate, 2),
                    "time_without_cs": round(time_without_cs, 1),
                    "total_cs": telemetry.cs,
                    "expected_min_cs_rate": self.cs_crash_threshold,
                },
            )

        return None

    def reset(self) -> None:
        """Clears all internal cooldown trackers and state."""
        self._trigger_cooldowns.clear()
        self._last_global_trigger_time = -9999.0
