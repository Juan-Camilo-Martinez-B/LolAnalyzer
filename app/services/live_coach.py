"""
Turns the live match into coach advice. The desktop polls this.

Situational rules (low health, mana, unspent gold, death timer, objectives,
vision) answer first with tips built from the match numbers. Tilt and farming
rules go through the language model (Groq, then Gemini, then a heuristic).
When nothing relevant happens, the coach stays quiet instead of inventing a tip.
"""

import asyncio
import logging
import time
from typing import Any

from app.engine.rules_evaluator import RulesEvaluator
from app.engine.situations import SituationAdvice, SituationCoach, parse_situation
from app.engine.sliding_window import SlidingWindowBuffer
from app.schemas.game_events import Role
from app.core.config import settings
from app.services.cloud_bridge import CloudUnavailable, cloud_post
from app.services.coach_metrics import record_advice
from app.services.gemini_service import gemini_coach_service
from app.services.live_game import fetch_live_game, parse_live_game
from app.services.riot_lcu import riot_lcu_service

logger = logging.getLogger("lol_analyzer.live_coach")

CACHE_SECONDS = 4.0


def _advice_source(generated_by: str) -> str:
    """Maps the model that wrote a tip to the stored advisor name."""
    label = (generated_by or "").lower()
    if not label or "heuristic" in label:
        return "heuristic"
    if label.startswith("gemini"):
        return "gemini"
    # Any other model name (gpt-oss, llama, ...) comes from Groq.
    return "groq"


class LiveCoach:
    def __init__(self) -> None:
        self.buffer = SlidingWindowBuffer()
        self.rules = RulesEvaluator()
        self.situations = SituationCoach()
        self.seen_events: set[str] = set()
        self.last_advice: dict[str, Any] | None = None
        self._advice_count = 0
        self._pending: set[asyncio.Task[None]] = set()
        self._last_game_time = 0.0
        self._lock = asyncio.Lock()
        self._cached_at = 0.0
        self._cached_body: dict[str, Any] | None = None
        self._saved_key: int | None = None

    def _forward(self, advice: dict[str, Any], token: str) -> None:
        """Fire-and-forget: a slow cloud must not delay the live HUD."""
        async def send() -> None:
            try:
                await cloud_post("/api/coach/advice", token, advice)
            except CloudUnavailable as exc:
                logger.info("Could not store live advice in the cloud: %s", exc.message)

        task = asyncio.create_task(send())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _publish(self, advice: dict[str, Any]) -> None:
        self._advice_count += 1
        advice["id"] = self._advice_count
        self.last_advice = advice
        logger.info("Live advice (%s): %s", advice.get("source"), advice.get("text"))

    @staticmethod
    def _situation_advice(tip: SituationAdvice, telemetry: Any) -> dict[str, Any]:
        return {
            "text": tip.text,
            "severity": tip.severity,
            "trigger": tip.key.upper(),
            "category": tip.category,
            "champion": telemetry.champion_name,
            "role": telemetry.role.value if isinstance(telemetry.role, Role) else str(telemetry.role),
            "gameTime": telemetry.game_time_seconds,
            "source": "rules",
        }

    def reset_match(self) -> None:
        self.buffer.reset()
        self.rules.reset()
        self.situations.reset()
        self.seen_events.clear()
        self.last_advice = None
        self._last_game_time = 0.0

    async def snapshot(self, db: Any = None, user_id: int | None = None, forward_token: str | None = None) -> dict[str, Any]:
        now = time.monotonic()
        if self._cached_body is not None and (now - self._cached_at) < CACHE_SECONDS:
            return self._cached_body

        async with self._lock:
            now = time.monotonic()
            if self._cached_body is not None and (now - self._cached_at) < CACHE_SECONDS:
                return self._cached_body
            body = await self._read(db, user_id, forward_token)
            self._cached_body = body
            self._cached_at = time.monotonic()
            return body

    async def _read(self, db: Any = None, user_id: int | None = None, forward_token: str | None = None) -> dict[str, Any]:
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

        telemetry, events, aliases = parsed
        if telemetry.game_time_seconds + 15 < self._last_game_time:
            self.reset_match()
        self._last_game_time = telemetry.game_time_seconds

        for event_id, event in events:
            if event_id in self.seen_events:
                continue
            self.seen_events.add(event_id)
            self.buffer.add_event(event)
        self.buffer.update_telemetry(telemetry)

        situation = parse_situation(payload, aliases) if payload else None
        tip = self.situations.evaluate(situation) if situation else None
        if tip is not None:
            self._publish(self._situation_advice(tip, telemetry))
        else:
            trigger = self.rules.evaluate(self.buffer, telemetry)
            if trigger is not None:
                try:
                    advice = await gemini_coach_service.generate_tactical_advice(trigger)
                    self._publish({
                        "text": advice.text,
                        "severity": advice.severity.value,
                        "trigger": advice.trigger_type.value,
                        "category": "tilt" if advice.trigger_type.value == "TILT_RISK" else "farm",
                        "champion": advice.champion_name,
                        "role": advice.role.value,
                        "gameTime": advice.game_time_seconds,
                        "source": _advice_source(advice.generated_by),
                    })
                except Exception as error:
                    logger.warning("Live advice failed: %s", error)

        if self.last_advice and self.last_advice.get("id") != self._saved_key and settings.mode == "local" and forward_token:
            # The agent has no database: the cloud stores the tip on the account.
            self._saved_key = self.last_advice.get("id")
            self._forward(dict(self.last_advice), forward_token)
        elif self.last_advice and self.last_advice.get("id") != self._saved_key and db is not None and user_id is not None:
            try:
                await record_advice(db, user_id, self.last_advice)
                self._saved_key = self.last_advice.get("id")
            except Exception as save_error:
                logger.warning("Could not store live advice: %s", save_error)

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
