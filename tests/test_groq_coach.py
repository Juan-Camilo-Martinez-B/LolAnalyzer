"""Compact Groq prompts and the in-memory advice cache."""

import asyncio

from app.core.prompts import COMPACT_SYSTEM_PROMPT, build_compact_coach_prompt, coach_state_key
from app.schemas.game_events import PlayerTelemetry, Role, RuleSeverity, RuleTrigger, TriggerType
from app.services.groq_coach import (
    DEFAULT_GROQ_MODEL,
    GROQ_CHAT_URL,
    GROQ_MAX_TOKENS,
    REASONING_TOKEN_MARGIN,
    GROQ_TEMPERATURE,
    GroqCoach,
    resolve_groq_model,
)


def _trigger(**context) -> RuleTrigger:
    return RuleTrigger(
        trigger_type=TriggerType.TILT_RISK,
        severity=RuleSeverity.CRITICAL,
        detected_at_game_time=360.0,
        reason="Riesgo de tilt",
        telemetry_snapshot=PlayerTelemetry(
            summoner_name="Player",
            champion_name="LeBlanc",
            role=Role.MID,
            level=6,
            kills=0,
            deaths=3,
            assists=1,
            cs=42,
            game_time_seconds=360.0,
        ),
        context_data=context
        or {
            "phase": "lane",
            "recent_deaths": 3,
        },
    )


def _bloated(trigger: RuleTrigger) -> RuleTrigger:
    trigger.context_data = {
        **trigger.context_data,
        "match": {"participants": ["Z" * 4000], "timeline": {"raw": "Q" * 4000}},
        "events": [{"type": "kill", "payload": "Y" * 2000}],
    }
    return trigger


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.status_code = status_code
        self._text = text

    def json(self):
        return {"choices": [{"message": {"content": self._text}}]}


def test_compact_prompt_keeps_metrics_and_drops_raw_json():
    trigger = _bloated(_trigger())
    prompt = build_compact_coach_prompt(trigger)

    assert "champ=LeBlanc" in prompt
    assert "kda=0/3/1" in prompt
    assert "cs/min=7.0" in prompt
    assert "recent_deaths=3" in prompt
    assert "phase=lane" in prompt
    assert "alert=TILT_RISK" in prompt
    assert "ZZZ" not in prompt
    assert "QQQ" not in prompt
    assert "YYY" not in prompt
    assert "participants" not in prompt
    assert len(prompt) < 240
    assert len(COMPACT_SYSTEM_PROMPT) < 180
    assert "TOP LANE" not in COMPACT_SYSTEM_PROMPT
    assert coach_state_key(trigger) == coach_state_key(_trigger())


def test_default_model_is_the_small_groq_model():
    assert resolve_groq_model(None, None) == DEFAULT_GROQ_MODEL
    assert resolve_groq_model("", "  ") == "openai/gpt-oss-20b"
    assert resolve_groq_model(None, "openai/gpt-oss-120b") == "openai/gpt-oss-120b"


def test_cache_skips_the_second_call_when_state_is_unchanged():
    calls = {"n": 0, "payload": None, "headers": None, "url": None}

    async def poster(url, payload, headers):
        calls["n"] += 1
        calls["url"] = url
        calls["payload"] = payload
        calls["headers"] = headers
        return _Response("Farmea bajo torre y no persigas.")

    async def run():
        coach = GroqCoach(api_key="test-key", model=DEFAULT_GROQ_MODEL, poster=poster)
        first = _bloated(_trigger())
        second = _bloated(_trigger())
        second.context_data["match"] = {"blob": "W" * 8000}
        text_a = await coach.advise(first)
        text_b = await coach.advise(second)
        changed = _trigger(phase="lane", recent_deaths=4)
        text_c = await coach.advise(changed)
        return text_a, text_b, text_c

    text_a, text_b, text_c = asyncio.run(run())

    assert text_a == "Farmea bajo torre y no persigas."
    assert text_b == text_a
    assert calls["n"] == 2
    assert text_c == text_a
    assert calls["url"] == GROQ_CHAT_URL
    assert calls["payload"]["model"] == "openai/gpt-oss-20b"
    assert calls["payload"]["temperature"] == GROQ_TEMPERATURE
    # gpt-oss reasons first, so it gets low effort and a margin over the answer cap.
    assert calls["payload"]["reasoning_effort"] == "low"
    assert calls["payload"]["max_tokens"] == GROQ_MAX_TOKENS + REASONING_TOKEN_MARGIN
    assert calls["payload"]["messages"][0]["content"] == COMPACT_SYSTEM_PROMPT
    assert "ZZZ" not in calls["payload"]["messages"][1]["content"]
    assert calls["headers"]["Authorization"] == "Bearer test-key"
    assert len(calls["payload"]["messages"]) == 2


def test_non_reasoning_models_keep_the_plain_cap():
    calls = {}

    async def poster(url, payload, headers):
        calls["payload"] = payload
        return _Response("Juega seguro.")

    coach = GroqCoach(api_key="test-key", model="some/plain-model", poster=poster)
    asyncio.run(coach.advise(_trigger()))
    assert calls["payload"]["max_tokens"] == GROQ_MAX_TOKENS
    assert "reasoning_effort" not in calls["payload"]
