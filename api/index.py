"""
Vercel entry point. Builds the cloud API: accounts, Riot API, stored history,
analysis and AI. League client routes live in the desktop app's local agent.
"""

import os

os.environ.setdefault("APP_MODE", "cloud")
os.environ.setdefault("LCU_AUTO_CONNECT", "false")
os.environ.setdefault("INIT_DB_ON_STARTUP", "false")
# Only /tmp is writable on Vercel; the Riot cache is a throwaway SQLite file there.
os.environ.setdefault("CACHE_DATABASE_URL", "sqlite+aiosqlite:////tmp/lol_analyzer_cache.db")

from app.main import create_app  # noqa: E402 - settings must see the environment first

app = create_app("cloud")
