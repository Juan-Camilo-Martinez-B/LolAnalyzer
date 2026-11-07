"""
Groq tactical coach.

Uses the OpenAI-compatible chat endpoint. The API key comes from GROQ_API_KEY.
Requests stay small: one stable system line, one metrics line, a low token cap,
and an in-memory cache keyed by the board state.
"""

import logging
import re
from typing import Any, Awaitable, Callable, Optional

import httpx

from app.core.config import settings
from app.core.prompts import COMPACT_SYSTEM_PROMPT, build_compact_coach_prompt, coach_state_key
from app.schemas.game_events import RuleTrigger

logger = logging.getLogger("lol_analyzer.groq_coach")

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_TEMPERATURE = 0.2
GROQ_MAX_TOKENS = 48

Poster = Callable[[str, dict[str, Any], dict[str, str]], Awaitable[Any]]


def resolve_groq_model(explicit: Optional[str] = None, configured: Optional[str] = None) -> str:
    chosen = (explicit or configured or "").strip()
    return chosen or DEFAULT_GROQ_MODEL


def limit_advice(text: str, max_words: int = 12) -> str:
    cleaned = re.sub(r'["`*_\n\r]', " ", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    words = cleaned.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]) + "..."
    return " ".join(words)


class GroqCoach:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        poster: Optional[Poster] = None,
    ) -> None:
        self._api_key_override = api_key
        self._model_override = model
        self._poster = poster
        self._cache: dict[str, str] = {}

    @property
    def model_name(self) -> str:
        configured = settings.GROQ_MODEL if self._model_override is None else self._model_override
        if self._model_override is not None:
            return resolve_groq_model(self._model_override, None)
        return resolve_groq_model(None, configured)

    def _api_key(self) -> str:
        if self._api_key_override is not None:
            return self._api_key_override.strip()
        return (settings.GROQ_API_KEY or "").strip()

    async def advise(self, trigger: RuleTrigger) -> Optional[str]:
        state = coach_state_key(trigger)
        cached = self._cache.get(state)
        if cached is not None:
            return cached
        api_key = self._api_key()
        if not api_key:
            return None
        raw = await self._complete(api_key, build_compact_coach_prompt(trigger))
        if not raw:
            return None
        text = limit_advice(raw)
        if not text:
            return None
        self._cache[state] = text
        return text

    async def _complete(self, api_key: str, prompt: str) -> Optional[str]:
        payload = {
            "model": self.model_name,
            "temperature": GROQ_TEMPERATURE,
            "max_tokens": GROQ_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": COMPACT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            if self._poster is not None:
                response = await self._poster(GROQ_CHAT_URL, payload, headers)
            else:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    response = await client.post(GROQ_CHAT_URL, json=payload, headers=headers)
        except Exception as exc:
            logger.warning("Groq request failed: %s", type(exc).__name__)
            return None

        status_code = getattr(response, "status_code", 0)
        if status_code != 200:
            logger.info("Groq returned status %s", status_code)
            return None
        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        if not isinstance(content, str):
            return None
        return content


groq_coach = GroqCoach()
