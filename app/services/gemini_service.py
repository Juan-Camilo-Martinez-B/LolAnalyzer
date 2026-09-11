"""
LolAnalyzer Backend - Google Gemini 1.5 Flash AI Tactical Coach Service
Provides ultra-fast, asynchronous tactical advice generation via the official google-genai SDK
with deterministic heuristic fallbacks for offline or missing API key scenarios.
"""

import asyncio
import logging
import re
import time
import uuid
from typing import Optional

from app.core.config import settings
from app.core.prompts import build_trigger_prompt, get_system_prompt_for_role
from app.schemas.game_events import (
    CoachAdvice,
    PlayerTelemetry,
    Role,
    RuleSeverity,
    RuleTrigger,
    TriggerType,
)

logger = logging.getLogger("lol_analyzer.gemini_service")

# Try importing the official google-genai SDK
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("google-genai SDK not installed or unavailable. Using heuristic fallback coach.")


class GeminiCoachService:
    """
    Asynchronous tactical advisor leveraging Google Gemini 1.5 Flash.
    Generates imperative, role-contextual advice in <12 words.
    """

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key: str = api_key if api_key is not None else settings.GEMINI_API_KEY
        self.model_name: str = model_name or settings.GEMINI_MODEL_NAME
        self._client: Optional[Any] = None

        if GENAI_AVAILABLE and self.api_key and self.api_key.strip() and self.api_key != "tu_api_key_aqui":
            try:
                self._client = genai.Client(api_key=self.api_key)
                logger.info(f"Gemini Coach Client initialized with model {self.model_name}.")
            except Exception as e:
                logger.error(f"Failed to initialize GenAI Client: {e}")
                self._client = None
        else:
            logger.info("GenAI Client offline (API key empty or placeholder). Heuristic fallback active.")

    def is_ai_ready(self) -> bool:
        """Returns True if Google GenAI client is authenticated and ready."""
        return self._client is not None

    async def generate_tactical_advice(self, trigger: RuleTrigger) -> CoachAdvice:
        """
        Generates tactical advice for a detected RuleTrigger.
        Dispatches to Gemini 1.5 Flash when available, or executes local deterministic fallback.
        """
        t = trigger.telemetry_snapshot
        advice_id = str(uuid.uuid4())[:8]

        # If AI client is active, attempt Gemini 1.5 Flash generation
        if self._client is not None:
            try:
                system_instruction = get_system_prompt_for_role(t.role)
                user_prompt = build_trigger_prompt(trigger)

                # Async call using Gemini SDK client.aio
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.3,
                    max_output_tokens=40,
                )

                response = await self._client.aio.models.generate_content(
                    model=self.model_name,
                    contents=user_prompt,
                    config=config,
                )

                raw_text = response.text.strip() if response and response.text else ""
                cleaned_text = self._clean_and_truncate_text(raw_text)

                if cleaned_text:
                    return CoachAdvice(
                        advice_id=advice_id,
                        trigger_type=trigger.trigger_type,
                        severity=trigger.severity,
                        text=cleaned_text,
                        role=t.role,
                        champion_name=t.champion_name,
                        game_time_seconds=t.game_time_seconds,
                        generated_by=self.model_name,
                    )
            except Exception as e:
                logger.warning(f"Gemini generation call failed ({e}). Falling back to heuristic rules.")

        # Heuristic fallback (fast, deterministic, zero-latency)
        fallback_text = self._generate_heuristic_fallback(trigger)
        return CoachAdvice(
            advice_id=advice_id,
            trigger_type=trigger.trigger_type,
            severity=trigger.severity,
            text=fallback_text,
            role=t.role,
            champion_name=t.champion_name,
            game_time_seconds=t.game_time_seconds,
            generated_by="heuristic_fallback",
        )

    def _clean_and_truncate_text(self, text: str, max_words: int = 12) -> str:
        """Strips markdown, quotes, trailing characters, and limits word count to 12."""
        # Remove quotes and markdown markers
        text = re.sub(r'["`*_\n\r]', ' ', text).strip()
        text = re.sub(r'\s+', ' ', text)

        words = text.split()
        if len(words) > max_words:
            words = words[:max_words]
            return " ".join(words) + "..."
        return " ".join(words)

    def _generate_heuristic_fallback(self, trigger: RuleTrigger) -> str:
        """
        Deterministic, role-tailored tactical fallback messages (<12 words).
        """
        t = trigger.telemetry_snapshot
        ttype = trigger.trigger_type

        if ttype == TriggerType.TILT_RISK:
            if t.role == Role.TOP:
                return "Congela la oleada bajo tu torre y espera al jungla."
            elif t.role == Role.MID:
                return "Evita trades 1v1 y guarda visión en los arbustos laterales."
            elif t.role == Role.ADC:
                return "Farmea detrás de tu soporte y evita pelear sin ventaja."
            elif t.role == Role.JUNGLE:
                return "Farmea tus campamentos seguros y evita invadir sin prioridad."
            elif t.role == Role.SUPPORT:
                return "Agrupa con tu equipo y mantén visión defensiva en entradas."
            return "Juega defensivo, estabiliza el mapa y evita sobre-extenderte."

        elif ttype == TriggerType.FORCED_FIGHT_NO_SUMMONERS:
            cd = int(trigger.context_data.get("flash_cooldown_remaining", 180))
            return f"Evita pelear hasta recuperar tu Destello en {cd} segundos."

        elif ttype == TriggerType.CS_CRASH:
            return "Prioriza limpiar oleadas seguras bajo torre antes de rotar."

        elif ttype == TriggerType.OBJECTIVE_CONTEST_RISK:
            return "Asegura visión en el río antes de iniciar el objetivo."

        return "Mantén tu posición y juega en torno a tu equipo."


# Global singleton instance
gemini_coach_service = GeminiCoachService()
