"""
Unit tests for LolAnalyzer Engine (SlidingWindowBuffer & RulesEvaluator).
"""

import pytest
from app.engine.sliding_window import SlidingWindowBuffer
from app.engine.rules_evaluator import RulesEvaluator
from app.schemas.game_events import (
    GameEvent,
    GameEventType,
    PlayerTelemetry,
    Role,
    RuleSeverity,
    SummonerSpellState,
    SummonerSpellType,
    TriggerType,
)


@pytest.fixture
def empty_buffer():
    return SlidingWindowBuffer(window_seconds=180)


@pytest.fixture
def rules_evaluator():
    # Cooldown of 0 seconds for isolated deterministic testing
    evaluator = RulesEvaluator(
        tilt_death_threshold=2,
        cs_crash_threshold=4.0,
        advice_cooldown_seconds=0
    )
    # Clear internal cooldowns
    evaluator._cooldown_durations = {t: 0.0 for t in evaluator._cooldown_durations}
    return evaluator


class TestSlidingWindowBuffer:
    def test_event_expiration(self, empty_buffer):
        buffer = empty_buffer
        
        # Add events at t=10, 50, 100
        buffer.add_event(GameEvent(event_type=GameEventType.KILL, game_time_seconds=10.0))
        buffer.add_event(GameEvent(event_type=GameEventType.KILL, game_time_seconds=50.0))
        buffer.add_event(GameEvent(event_type=GameEventType.DEATH, game_time_seconds=100.0))
        
        # At t=200, window 180s threshold is 20s. Event at t=10 is purged.
        buffer.purge_expired(current_game_time=200.0)
        assert buffer.get_recent_deaths_count(current_game_time=200.0) == 1
        assert buffer.get_recent_kills_and_assists_count(current_game_time=200.0) == 1

    def test_cs_rate_calculation(self, empty_buffer):
        buffer = empty_buffer
        
        # t=100: 20 CS, t=160 (1 min later): 30 CS -> +10 CS in 1 min = 10.0 CS/min
        t1 = PlayerTelemetry(cs=20, game_time_seconds=100.0)
        t2 = PlayerTelemetry(cs=30, game_time_seconds=160.0)
        
        buffer.update_telemetry(t1)
        buffer.update_telemetry(t2)
        
        rate = buffer.get_recent_cs_rate(current_game_time=160.0, window_seconds=120.0)
        assert pytest.approx(rate, 0.1) == 10.0

    def test_summoner_spell_cooldown_tracking(self, empty_buffer):
        buffer = empty_buffer
        
        # Flash used at t=300 with 300s cooldown
        buffer.record_summoner_usage(
            spell_type=SummonerSpellType.FLASH,
            slot="D",
            used_at_game_time=300.0,
            cooldown_seconds=300.0
        )
        
        # At t=350, Flash should NOT be ready (250s cooldown remaining)
        assert not buffer.is_summoner_ready(SummonerSpellType.FLASH, current_game_time=350.0)
        assert buffer.get_summoner_cooldown_remaining(SummonerSpellType.FLASH, current_game_time=350.0) == 250.0
        
        # At t=601, Flash SHOULD be ready
        assert buffer.is_summoner_ready(SummonerSpellType.FLASH, current_game_time=601.0)
        assert buffer.get_summoner_cooldown_remaining(SummonerSpellType.FLASH, current_game_time=601.0) == 0.0


class TestRulesEvaluator:
    def test_tilt_risk_trigger_on_rapid_deaths(self, empty_buffer, rules_evaluator):
        buffer = empty_buffer
        evaluator = rules_evaluator
        
        # 1st death at t=120
        d1 = GameEvent(event_type=GameEventType.DEATH, game_time_seconds=120.0)
        t1 = PlayerTelemetry(deaths=1, game_time_seconds=120.0, role=Role.MID, champion_name="Ahri")
        buffer.add_event(d1)
        buffer.update_telemetry(t1)
        trigger1 = evaluator.evaluate(buffer, t1)
        assert trigger1 is None  # Only 1 death, threshold is 2
        
        # 2nd death at t=200 (within 80 seconds)
        d2 = GameEvent(event_type=GameEventType.DEATH, game_time_seconds=200.0)
        t2 = PlayerTelemetry(deaths=2, game_time_seconds=200.0, role=Role.MID, champion_name="Ahri")
        buffer.add_event(d2)
        buffer.update_telemetry(t2)
        trigger2 = evaluator.evaluate(buffer, t2)
        
        assert trigger2 is not None
        assert trigger2.trigger_type == TriggerType.TILT_RISK
        assert trigger2.severity == RuleSeverity.WARNING

    def test_forced_fight_without_flash_trigger(self, empty_buffer, rules_evaluator):
        buffer = empty_buffer
        evaluator = rules_evaluator
        
        # Flash used at t=300 (300s cooldown)
        buffer.record_summoner_usage(
            spell_type=SummonerSpellType.FLASH,
            slot="D",
            used_at_game_time=300.0,
            cooldown_seconds=300.0
        )
        
        # Player dies at t=360 (Flash has 240s remaining)
        d = GameEvent(event_type=GameEventType.DEATH, game_time_seconds=360.0)
        t = PlayerTelemetry(
            deaths=1,
            game_time_seconds=360.0,
            role=Role.ADC,
            champion_name="Jinx",
            summoner_spells=[
                SummonerSpellState(
                    spell_name=SummonerSpellType.FLASH,
                    slot="D",
                    is_ready=False,
                    cooldown_remaining_seconds=240.0
                )
            ]
        )
        buffer.add_event(d)
        buffer.update_telemetry(t)
        
        trigger = evaluator.evaluate(buffer, t)
        assert trigger is not None
        assert trigger.trigger_type == TriggerType.FORCED_FIGHT_NO_SUMMONERS
        assert trigger.severity == RuleSeverity.WARNING

    def test_cs_crash_trigger_and_support_immunity(self, empty_buffer, rules_evaluator):
        buffer = empty_buffer
        evaluator = rules_evaluator
        
        # Top laner at t=300 with 15 CS, at t=420 still 15 CS (90s without CS)
        t1 = PlayerTelemetry(cs=15, game_time_seconds=300.0, role=Role.TOP, champion_name="Darius")
        t2 = PlayerTelemetry(cs=15, game_time_seconds=420.0, role=Role.TOP, champion_name="Darius")
        
        buffer.update_telemetry(t1)
        buffer.update_telemetry(t2)
        
        top_trigger = evaluator.evaluate(buffer, t2)
        assert top_trigger is not None
        assert top_trigger.trigger_type == TriggerType.CS_CRASH
        
        # Support with same CS situation should NOT trigger CS crash
        buffer.reset()
        evaluator.reset()
        s1 = PlayerTelemetry(cs=5, game_time_seconds=300.0, role=Role.SUPPORT, champion_name="Thresh")
        s2 = PlayerTelemetry(cs=5, game_time_seconds=420.0, role=Role.SUPPORT, champion_name="Thresh")
        buffer.update_telemetry(s1)
        buffer.update_telemetry(s2)
        
        sup_trigger = evaluator.evaluate(buffer, s2)
        assert sup_trigger is None
