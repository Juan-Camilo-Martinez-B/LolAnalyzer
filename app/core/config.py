"""
LolAnalyzer Backend - Core Configuration
Manages environment variables, server settings, and engine thresholds using Pydantic Settings.
"""

from functools import lru_cache
from typing import List, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    # Application Info
    APP_NAME: str = "LolAnalyzer Backend"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # Server Configuration
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # CORS Configuration
    ALLOWED_ORIGINS: Union[List[str], str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "overwolf-extension://*"
    ]

    # Google Gemini AI Configuration
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL_NAME: str = "gemini-1.5-flash"

    # Riot LCU (League Client Update) Settings
    LCU_POLL_INTERVAL: float = 2.0
    LCU_AUTO_CONNECT: bool = True
    LCU_TIMEOUT: float = 5.0

    # Heuristic Engine & Sliding Window Thresholds
    SLIDING_WINDOW_SECONDS: int = 180  # 3-minute sliding window
    TILT_DEATH_THRESHOLD: int = 2       # >=2 deaths in sliding window triggers tilt risk
    CS_CRASH_THRESHOLD: float = 4.0     # CS/min below 4.0 triggers CS crash alert
    COOLDOWN_BETWEEN_ADVICE_SECONDS: int = 30  # Cooldown between consecutive AI tactical tips

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, list):
            return v
        return ["*"]


@lru_cache()
def get_settings() -> Settings:
    """Returns a cached instance of application settings."""
    return Settings()


settings: Settings = get_settings()
