"""
Unit tests for GeminiCoachService (Prompts, word limits, and tactical advice generation).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.prompts import build_trigger_prompt, get_system_prompt_for_role
from app.schemas.game_events import (
    PlayerTelemetry,
    Role,
    RuleSeverity,
    RuleTrigger,
    TriggerType,
)
from app.services.gemini_service import GeminiCoachService


@pytest.fixture
def mock_trigger():
    return RuleTrigger(
        trigger_type=TriggerType.TILT_RISK,
        severity=RuleSeverity.CRITICAL,
        detected_at_game_time=360.0,
        reason="Riesgo de tilt: 3 muertes consecutivas en 3 minutos",
        telemetry_snapshot=PlayerTelemetry(
            summoner_name="Faker",
            champion_name="LeBlanc",
            role=Role.MID,
            level=6,
            kills=0,
            deaths=3,
            assists=0,
            cs=45,
            game_time_seconds=360.0,
        ),
        context_data={"recent_deaths": 3},
    )


class TestGeminiService:
    def test_system_prompt_role_inclusion(self):
        mid_prompt = get_system_prompt_for_role(Role.MID)
        top_prompt = get_system_prompt_for_role(Role.TOP)
        
        assert "MID LANE" in mid_prompt
        assert "12 palabras" in mid_prompt
        assert "TOP LANE" in top_prompt

    def test_trigger_prompt_builder(self, mock_trigger: RuleTrigger):
        prompt = build_trigger_prompt(mock_trigger)
        assert "LeBlanc" in prompt
        assert "MID" in prompt
        assert "TILT_RISK" in prompt
        assert "0/3/0" in prompt

    def test_heuristic_fallback_generation(self, mock_trigger: RuleTrigger):
        # Service without API key defaults to heuristic fallback
        async def run_test():
            service = GeminiCoachService(api_key="")
            advice = await service.generate_tactical_advice(mock_trigger)
            
            assert advice is not None
            assert advice.trigger_type == TriggerType.TILT_RISK
            assert advice.role == Role.MID
            assert advice.generated_by == "heuristic_fallback"
            
            # Verify strict length: <= 12 words
            word_count = len(advice.text.split())
            assert word_count <= 12
            assert len(advice.text) > 0

        import asyncio
        asyncio.run(run_test())

    def test_gemini_api_mock_generation(self, mock_trigger: RuleTrigger):
        async def run_test():
            service = GeminiCoachService(api_key="mock_test_key")
            
            # Mock the genai client
            mock_response = MagicMock()
            mock_response.text = "Farmea seguro bajo torre y espera al jungla."
            
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
            service._client = mock_client
            
            advice = await service.generate_tactical_advice(mock_trigger)
            assert advice is not None
            assert advice.text == "Farmea seguro bajo torre y espera al jungla."
            assert advice.generated_by == service.model_name
            assert len(advice.text.split()) <= 12

        import asyncio
        asyncio.run(run_test())
