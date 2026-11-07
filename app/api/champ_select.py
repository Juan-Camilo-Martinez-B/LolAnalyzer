"""
LolAnalyzer Backend - Champion Select REST API
Exposes endpoints for the desktop frontend to query live LCU Champ Select state,
summoner profile, and client connection status.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.access import bearer_token, require_player
from app.db.models import User
from app.services.loadout import (
    LoadoutError,
    apply_summoner_spells,
    client_connected,
    import_item_set,
    import_rune_page,
    meta_loadout,
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


class SpellImportRequest(BaseModel):
    # Order matters: the first spell goes on D, the second on F.
    spell_ids: list[int] = Field(..., min_length=2, max_length=2)


class ItemBlock(BaseModel):
    title: str = Field(..., min_length=1, max_length=60)
    item_ids: list[int] = Field(..., min_length=1, max_length=12)


class ItemImportRequest(BaseModel):
    champion_id: int = Field(..., ge=1, le=9999)
    champion_name: str = Field(..., min_length=1, max_length=40)
    blocks: list[ItemBlock] = Field(..., min_length=1, max_length=6)


def _raise_loadout(error: LoadoutError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get("/loadout")
async def get_loadout(
    champion_id: int = Query(..., ge=1, le=9999),
    position: Optional[str] = Query(default=None, max_length=12),
    opponent_id: Optional[int] = Query(default=None, ge=1, le=9999),
    _player: Optional[User] = Depends(require_player),
    forward_token: Optional[str] = Depends(bearer_token),
) -> Dict[str, Any]:
    """Meta runes and items from OP.GG (against the lane opponent when known), plus client pages."""
    if not await client_connected():
        return {
            "clientConnected": False,
            "championId": champion_id,
            "runePages": [],
            "itemBlocks": [],
            "opponent": None,
            "source": "none",
            "notice": "Abre el cliente de League para ver runas y objetos.",
        }
    body = await meta_loadout(champion_id, position, opponent_id, forward_token)
    return {"clientConnected": True, **body}


@router.post("/loadout/runes")
async def post_rune_import(
    body: RuneImportRequest,
    _player: Optional[User] = Depends(require_player),
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
    return {"imported": True, "page": page}


@router.post("/loadout/spells")
async def post_spell_import(
    body: SpellImportRequest,
    _player: Optional[User] = Depends(require_player),
) -> Dict[str, Any]:
    """Sets the summoner spells in champion select: spell_ids[0] on D, spell_ids[1] on F."""
    first, second = body.spell_ids
    if first == second or not all(0 < spell <= 100 for spell in body.spell_ids):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Elige dos hechizos distintos.")
    if not await apply_summoner_spells(body.spell_ids):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Los hechizos solo se pueden cambiar durante la selección de campeón.",
        )
    return {"imported": True, "spellIds": body.spell_ids}


@router.post("/loadout/items")
async def post_item_import(
    body: ItemImportRequest,
    _player: Optional[User] = Depends(require_player),
) -> Dict[str, Any]:
    """Writes the item set to the client. Repeating it replaces the same set."""
    blocks = [{"title": block.title.strip(), "itemIds": block.item_ids} for block in body.blocks]
    try:
        item_set = await import_item_set(body.champion_id, body.champion_name.strip(), blocks)
    except LoadoutError as error:
        _raise_loadout(error)
    return {"imported": True, "itemSet": item_set}
