"""
LolAnalyzer Backend - Champion Select REST API
Exposes endpoints for the desktop frontend to query live LCU Champ Select state,
summoner profile, and client connection status.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status

from app.schemas.game_events import ChampSelectSession, Role
from app.services.riot_lcu import riot_lcu_service

logger = logging.getLogger("lol_analyzer.champ_select")

router = APIRouter(prefix="/api/champ-select", tags=["Champion Select"])


@router.get("/status")
async def get_lcu_status() -> Dict[str, Any]:
    """
    Returns current LCU connection status, port, and lockfile discovery.
    """
    is_connected = await riot_lcu_service.ensure_connection()
    return {
        "connected": is_connected,
        "port": riot_lcu_service.port,
        "protocol": riot_lcu_service.protocol,
        "lockfile_path": str(riot_lcu_service.lockfile_path) if riot_lcu_service.lockfile_path else None,
    }


@router.get("/session")
async def get_champ_select_session() -> Dict[str, Any]:
    """
    Returns active Champion Select session from the local League Client.
    If client is not running or not in Champion Select, returns is_active=False.
    """
    session_data = await riot_lcu_service.get_champ_select_session()
    if not session_data:
        return {
            "is_active": False,
            "session": None,
            "message": "No active Champion Select session found.",
        }

    return {
        "is_active": True,
        "session": session_data,
        "message": "Active Champion Select session retrieved.",
    }


@router.get("/summoner")
async def get_current_summoner() -> Dict[str, Any]:
    """
    Returns active logged-in summoner profile information.
    """
    summoner = await riot_lcu_service.get_current_summoner()
    if not summoner:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="League Client not connected or summoner data unavailable.",
        )
    return summoner


@router.get("/gameflow")
async def get_gameflow_phase() -> Dict[str, Any]:
    """
    Returns current Gameflow phase (e.g., 'ChampSelect', 'InProgress', 'Lobby', 'None').
    """
    phase = await riot_lcu_service.get_gameflow_phase()
    return {
        "phase": phase or "None",
        "in_champ_select": phase == "ChampSelect",
        "in_game": phase == "InProgress",
    }
