"""
LolAnalyzer Backend - Core Configuration
Manages environment variables, server settings, database connections, and security parameters.
"""

from functools import lru_cache
import os
from pathlib import Path
from typing import List, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/core/config.py -> backend root
_BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    # Application Metadata
    APP_NAME: str = "LolAnalyzer Backend"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # Server Configuration
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # Deployment mode:
    #   full  - everything in one process (development).
    #   cloud - Vercel: accounts, Riot API, history, analysis and AI. No League client access.
    #   local - agent bundled in the desktop app: League client only, no secrets, no database.
    APP_MODE: str = "full"
    # Where the local agent forwards writes and AI requests (cloud API base URL).
    CLOUD_API_URL: str = ""
    # Random per-launch token the desktop sends to the local agent (X-Agent-Token).
    AGENT_TOKEN: str = ""
    # Serverless cold starts should not run create_all; migrations own the schema there.
    INIT_DB_ON_STARTUP: bool = True

    # CORS Configuration
    ALLOWED_ORIGINS: Union[List[str], str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "app://lolanalyzer",
        "overwolf-extension://*"
    ]

    # Database Configuration (PostgreSQL Cloud / SQLite async fallback)
    DATABASE_URL: str = ""
    # Tests can point this at another sqlite file. It never accepts Postgres.
    CACHE_DATABASE_URL: str = ""
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # Security & JWT Authentication
    SECRET_KEY: str = "lol_analyzer_jwt_super_secret_development_key_987654321"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Google OAuth 2.0
    GOOGLE_CLIENT_ID: str = ""

    # Google Gemini AI Configuration
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL_NAME: str = "gemini-1.5-flash"

    # Groq (OpenAI-compatible). The key stays on the server and is never hardcoded.
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-20b"

    # OP.GG public MCP endpoint for meta runes, items and lane matchups. No key is needed.
    OPGG_MCP_URL: str = "https://mcp-api.op.gg/mcp"
    OPGG_TIMEOUT_SECONDS: float = 15.0
    OPGG_CACHE_MINUTES: int = 30

    # Official Riot API key. Never expose this to the desktop client.
    # RSO (Riot Sign On) needs an approved production application and is not used here.
    RIOT_API_KEY: str = ""

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

    @property
    def async_database_url(self) -> str:
        """
        Returns an async-compatible database connection URL.
        If DATABASE_URL is not set or empty, falls back to local async SQLite.
        Converts 'postgres://' or 'postgresql://' to 'postgresql+asyncpg://'.
        """
        raw_url = self.DATABASE_URL.strip() if self.DATABASE_URL else ""
        if not raw_url:
            return "sqlite+aiosqlite:///./lol_analyzer.db"

        if raw_url.startswith("postgres://"):
            return raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif raw_url.startswith("postgresql://") and not raw_url.startswith("postgresql+asyncpg://"):
            return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

        return raw_url

    @property
    def cache_database_url(self) -> str:
        """
        Local SQLite file for Riot profile, match and Data Dragon cache.
        Account tables never live here, even when DATABASE_URL is empty.
        """
        raw_url = self.CACHE_DATABASE_URL.strip() if self.CACHE_DATABASE_URL else ""
        if raw_url:
            if not raw_url.startswith("sqlite"):
                raise ValueError("Riot cache must use a local SQLite database.")
            return raw_url
        if os.environ.get("VERCEL"):
            # Serverless: only /tmp is writable, and the cache is disposable.
            return "sqlite+aiosqlite:////tmp/lol_analyzer_cache.db"
        path = (_BACKEND_DIR / "lol_analyzer_cache.db").as_posix()
        return f"sqlite+aiosqlite:///{path}"


    @property
    def mode(self) -> str:
        value = (self.APP_MODE or "full").strip().lower()
        return value if value in {"full", "cloud", "local"} else "full"


@lru_cache()
def get_settings() -> Settings:
    """Returns a cached instance of application settings."""
    return Settings()


settings: Settings = get_settings()
