"""
LolAnalyzer Backend - Main FastAPI Application
Entry point for the backend server providing WebSocket streams for Overwolf overlay,
REST endpoints for Champion Select, and asynchronous tactical co-pilot pipeline.
"""

from contextlib import asynccontextmanager
import logging
import sys
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api import (
    auth_router,
    champ_select_router,
    profile_router,
    stats_router,
    websocket_router,
    ws_manager,
)
from app.core.config import settings
from app.services.riot_lcu import riot_lcu_service

# Logging Configuration
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("lol_analyzer.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages application startup and graceful shutdown.
    """
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Host: {settings.HOST}:{settings.PORT} | Debug: {settings.DEBUG}")

    # Initialize database tables
    try:
        from app.db.session import init_db
        await init_db()
    except Exception as e:
        logger.error(f"Database initialization error: {e}")

    # Attempt initial LCU discovery
    if settings.LCU_AUTO_CONNECT:
        try:
            connected = await riot_lcu_service.ensure_connection()
            if connected:
                logger.info(f"Connected to local League Client on port {riot_lcu_service.port}")
            else:
                logger.info("League Client not currently detected. Will auto-discover upon launch.")
        except Exception as e:
            logger.debug(f"Initial LCU check skipped: {e}")

    yield

    # Shutdown logic
    logger.info("Shutting down LolAnalyzer Backend...")
    await riot_lcu_service.close()
    logger.info("Shutdown complete.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Real-Time Tactical Co-Pilot and In-Game Assistant Backend for League of Legends",
    lifespan=lifespan,
)

# Configure CORS for Overwolf App (overwolf-extension://) and local Vite development
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS if isinstance(settings.ALLOWED_ORIGINS, list) else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(stats_router)
app.include_router(websocket_router)
app.include_router(champ_select_router)


@app.get("/", tags=["Health"])
async def root() -> Dict[str, Any]:
    """Root metadata endpoint."""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "online",
        "docs_url": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health_check() -> Dict[str, Any]:
    """Comprehensive health check endpoint."""
    lcu_connected = await riot_lcu_service.ensure_connection()
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "active_ws_connections": len(ws_manager.active_connections),
        "lcu_connected": lcu_connected,
        "lcu_port": riot_lcu_service.port if lcu_connected else None,
    }


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
