"""
Turns the live match into coach advice. The desktop polls this; Gemini is
called only when a rule fires or when a fresh tip is due.
"""

import asyncio
import logging
import time
from typing import Any

from app.engine.rules_evaluator import RulesEvaluator
from app.engine.sliding_window import SlidingWindowBuffer
from app.schemas.game_events import Role, RuleSeverity, RuleTrigger, TriggerType
from app.services.coach_metrics import record_advice
from app.services.gemini_service import gemini_coach_service
from app.services.live_game import fetch_live_game, parse_live_game
from app.services.riot_lcu import riot_lcu_service

logger = logging.getLogger("lol_analyzer.live_coach")

TIP_GAP_SECONDS = 75.0
CACHE_SECONDS = 4.0


class LiveCoach:
    def __init__(self) -> None:
        self.buffer = SlidingWindowBuffer()
        self.rules = RulesEvaluator()
        self.seen_events: set[str] = set()
        self.last_advice: dict[str, Any] | None = None
        self.last_tip_at = -9999.0
        self._last_game_time = 0.0
        self._lock = asyncio.Lock()
        self._cached_at = 0.0
        self._cached_body: dict[str, Any] | None = None
        self._saved_key: tuple[int, str, float] | None = None

    def reset_match(self) -> None:
        self.buffer.reset()
        self.rules.reset()
        self.seen_events.clear()
        self.last_advice = None
        self.last_tip_at = -9999.0
        self._last_game_time = 0.0

    async def snapshot(self, db: Any = None, user_id: int | None = None) -> dict[str, Any]:
        now = time.monotonic()
        if self._cached_body is not None and (now - self._cached_at) < CACHE_SECONDS:
            return self._cached_body

        async with self._lock:
            now = time.monotonic()
            if self._cached_body is not None and (now - self._cached_at) < CACHE_SECONDS:
                return self._cached_body
            body = await self._read(db, user_id)
            self._cached_body = body
            self._cached_at = time.monotonic()
            return body

    async def _read(self, db: Any = None, user_id: int | None = None) -> dict[str, Any]:
        phase = "None"
        try:
            phase = await riot_lcu_service.get_gameflow_phase() or "None"
        except Exception:
            phase = "None"

        payload = await fetch_live_game()
        parsed = parse_live_game(payload) if payload else None
        if parsed is None:
            if phase != "InProgress":
                self.reset_match()
            return {
                "in_game": phase == "InProgress",
                "phase": phase,
                "waiting": phase == "InProgress",
                "player": None,
                "advice": self.last_advice if phase == "InProgress" else None,
            }

        telemetry, events, _aliases = parsed
        if telemetry.game_time_seconds + 15 < self._last_game_time:
            self.reset_match()
        self._last_game_time = telemetry.game_time_seconds

        for event_id, event in events:
            if event_id in self.seen_events:
                continue
            self.seen_events.add(event_id)
            self.buffer.add_event(event)
        self.buffer.update_telemetry(telemetry)

        trigger = self.rules.evaluate(self.buffer, telemetry)
        if (
            trigger is None
            and telemetry.game_time_seconds >= 45
            and telemetry.game_time_seconds - self.last_tip_at >= TIP_GAP_SECONDS
        ):
            trigger = RuleTrigger(
                trigger_type=TriggerType.GENERAL_TACTICAL,
                severity=RuleSeverity.INFO,
                detected_at_game_time=telemetry.game_time_seconds,
                reason=(
                    f"Lectura en vivo de {telemetry.champion_name}: "
                    f"{telemetry.kills}/{telemetry.deaths}/{telemetry.assists}, {telemetry.cs} CS."
                ),
                telemetry_snapshot=telemetry,
            )

        if trigger is not None:
            self.last_tip_at = telemetry.game_time_seconds
            try:
                advice = await gemini_coach_service.generate_tactical_advice(trigger)
                source = "heuristic" if "heuristic" in advice.generated_by else "gemini"
                self.last_advice = {
                    "text": advice.text,
                    "severity": advice.severity.value,
                    "trigger": advice.trigger_type.value,
                    "champion": advice.champion_name,
                    "role": advice.role.value,
                    "gameTime": advice.game_time_seconds,
                    "source": source,
                }
                saved_key = (user_id or 0, advice.text, advice.game_time_seconds)
                if db is not None and user_id is not None and saved_key != self._saved_key:
                    try:
                        await record_advice(db, user_id, self.last_advice)
                        self._saved_key = saved_key
                    except Exception as save_error:
                        logger.warning("Could not store live advice: %s", save_error)
                logger.info("Live advice (%s): %s", source, advice.text)
            except Exception as error:
                logger.warning("Live advice failed: %s", error)

        return {
            "in_game": True,
            "phase": phase or "InProgress",
            "waiting": False,
            "player": {
                "champion": telemetry.champion_name,
                "role": telemetry.role.value if isinstance(telemetry.role, Role) else str(telemetry.role),
                "kills": telemetry.kills,
                "deaths": telemetry.deaths,
                "assists": telemetry.assists,
                "cs": telemetry.cs,
                "level": telemetry.level,
                "gold": telemetry.current_gold,
                "gameTime": telemetry.game_time_seconds,
            },
            "advice": self.last_advice,
        }


live_coach = LiveCoach()
