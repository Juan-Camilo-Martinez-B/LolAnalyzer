"""
LolAnalyzer Backend - FastAPI application factory.

One codebase, three deployments (APP_MODE):
  full  - everything in one process, for development.
  cloud - Vercel: accounts, Riot API, stored history, analysis and AI. No League
          client access, no WebSocket and no in-process state between requests.
  local - the agent bundled in the desktop app: champion select, imports and the
          live coach, reading the League client on 127.0.0.1. No secrets, no database.
"""

from contextlib import asynccontextmanager
import logging
import sys
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api.analysis import router as analysis_router
from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from app.api.champ_select import router as champ_select_router
from app.api.cloud_sync import router as cloud_sync_router
from app.api.live_coach import history_router as coach_history_router
from app.api.live_coach import live_router as coach_live_router
from app.api.local_bridge import router as local_bridge_router
from app.api.profile import router as profile_router
from app.api.riot import router as riot_router
from app.api.stats import router as stats_router
from app.api.websocket import manager as ws_manager
from app.api.websocket import router as websocket_router
from app.core.config import settings
from app.core.exceptions import install_exception_handlers
from app.services.riot_lcu import riot_lcu_service

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("lol_analyzer.main")

ACCOUNT_ROUTERS = [
    auth_router,
    riot_router,
    profile_router,
    stats_router,
    analytics_router,
    analysis_router,
    coach_history_router,
    cloud_sync_router,
]
CLIENT_ROUTERS = [champ_select_router, coach_live_router, local_bridge_router]

ROUTERS_BY_MODE = {
    "full": [*ACCOUNT_ROUTERS, *CLIENT_ROUTERS, websocket_router],
    "cloud": ACCOUNT_ROUTERS,
    "local": CLIENT_ROUTERS,
}


def _lifespan(mode: str):
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.info("Starting %s v%s (%s mode)", settings.APP_NAME, settings.APP_VERSION, mode)
        if mode != "local" and settings.INIT_DB_ON_STARTUP:
            try:
                from app.db.session import init_db
                await init_db()
            except Exception as exc:
                logger.error("Database initialization error: %s", exc)
        if mode != "cloud" and settings.LCU_AUTO_CONNECT:
            try:
                if await riot_lcu_service.ensure_connection():
                    logger.info("Connected to local League Client on port %s", riot_lcu_service.port)
                else:
                    logger.info("League Client not currently detected. Will auto-discover upon launch.")
            except Exception as exc:
                logger.debug("Initial LCU check skipped: %s", exc)
        yield
        if mode != "cloud":
            await riot_lcu_service.close()
        logger.info("Shutdown complete.")

    return lifespan


def _allowed_origins() -> list[str]:
    configured = settings.ALLOWED_ORIGINS if isinstance(settings.ALLOWED_ORIGINS, list) else []
    origins = [origin for origin in configured if origin and origin != "*"]
    return origins or ["http://localhost:5173", "http://127.0.0.1:5173", "app://lolanalyzer"]


def create_app(mode: str | None = None) -> FastAPI:
    """Builds the API for one deployment mode (defaults to APP_MODE)."""
    chosen = (mode or settings.mode).lower()
    if chosen not in ROUTERS_BY_MODE:
        raise ValueError(f"Unknown APP_MODE: {chosen}")

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Real-Time Tactical Co-Pilot and In-Game Assistant Backend for League of Legends",
        lifespan=_lifespan(chosen),
    )
    install_exception_handlers(app)

    # Credentials cannot be combined with a wildcard origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?|overwolf-extension://.*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in ROUTERS_BY_MODE[chosen]:
        app.include_router(router)

    @app.get("/", tags=["Health"])
    async def root() -> Dict[str, Any]:
        return {
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "mode": chosen,
            "status": "online",
            "docs_url": "/docs",
        }

    @app.get("/health", tags=["Health"])
    async def health_check() -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "status": "healthy",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "mode": chosen,
        }
        if chosen == "cloud":
            return body
        lcu_connected = await riot_lcu_service.ensure_connection()
        body.update({
            "active_ws_connections": len(ws_manager.active_connections),
            "lcu_connected": lcu_connected,
            "lcu_port": riot_lcu_service.port if lcu_connected else None,
        })
        return body

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
