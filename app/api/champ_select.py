"""
LolAnalyzer Backend - Champion Select REST API
Exposes endpoints for the desktop frontend to query live LCU Champ Select state,
summoner profile, and client connection status.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.loadout import (
    LoadoutError,
    apply_summoner_spells,
    client_connected,
    history_build,
    import_item_set,
    import_rune_page,
    recommended_rune_pages,
)
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


class RuneImportRequest(BaseModel):
    champion_name: str = Field(..., min_length=1, max_length=40)
    primary_style_id: int = Field(..., ge=8000, le=9999)
    sub_style_id: int = Field(..., ge=8000, le=9999)
    perk_ids: list[int] = Field(..., min_length=9, max_length=9)
    summoner_spell_ids: list[int] = Field(default_factory=list, max_length=2)


class ItemImportRequest(BaseModel):
    champion_id: int = Field(..., ge=1, le=9999)
    champion_name: str = Field(..., min_length=1, max_length=40)
    item_ids: list[int] = Field(..., min_length=1, max_length=8)
    games: int = Field(default=0, ge=0, le=1000)


def _raise_loadout(error: LoadoutError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get("/loadout")
async def get_loadout(
    champion_id: int = Query(..., ge=1, le=9999),
    champion_name: str = Query(..., min_length=1, max_length=40),
    position: Optional[str] = Query(default=None, max_length=12),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Client rune recommendations plus the player's usual build for the champion."""
    connected = await client_connected()
    pages = await recommended_rune_pages(champion_id, position) if connected else []
    build = await history_build(db, current_user.id, champion_name)
    return {
        "clientConnected": connected,
        "championId": champion_id,
        "championName": champion_name,
        "runePages": pages,
        "build": build,
    }


@router.post("/loadout/runes")
async def post_rune_import(
    body: RuneImportRequest,
    _current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Writes the rune page to the client. Repeating it replaces the same page."""
    try:
        page = await import_rune_page(
            body.champion_name.strip(),
            body.primary_style_id,
            body.sub_style_id,
            body.perk_ids,
        )
    except LoadoutError as error:
        _raise_loadout(error)
    spells_applied = await apply_summoner_spells(body.summoner_spell_ids) if body.summoner_spell_ids else False
    return {"imported": True, "page": page, "spellsApplied": spells_applied}


@router.post("/loadout/items")
async def post_item_import(
    body: ItemImportRequest,
    _current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Writes the item set to the client. Repeating it replaces the same set."""
    try:
        item_set = await import_item_set(body.champion_id, body.champion_name.strip(), body.item_ids, body.games)
    except LoadoutError as error:
        _raise_loadout(error)
    return {"imported": True, "itemSet": item_set}
