"""
Core module for LolAnalyzer Backend.
"""

from app.core.config import get_settings, settings
from app.core.prompts import build_trigger_prompt, get_system_prompt_for_role

__all__ = ["settings", "get_settings", "get_system_prompt_for_role", "build_trigger_prompt"]
