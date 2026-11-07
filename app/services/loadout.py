"""
Runes and item sets for champion select, read from and written to the local
League client.

Runes come from the client's own recommended pages. Items come from the
player's archived Summoner's Rift games with that champion. Both imports
replace the page or set that LolAnalyzer wrote before, so repeating an import
never piles up duplicates and never touches the player's own pages.
"""

from collections import Counter
import json
import logging
import re
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LocalMatchHistory
from app.services.lcu_match_map import SUMMONERS_RIFT_QUEUES
from app.services.riot_lcu import riot_lcu_service

logger = logging.getLogger("lol_analyzer.loadout")

PAGE_PREFIX = "LolAnalyzer"
SET_PREFIX = "LolAnalyzer - "
RIFT_MAP_ID = 11
MAX_BUILD_ITEMS = 6
HISTORY_SCAN_LIMIT = 200

# Positions as the client names them in recommended-pages.
POSITION_ALIASES = {
    "TOP": "top",
    "JUNGLE": "jungle",
    "MID": "middle",
    "MIDDLE": "middle",
    "ADC": "bottom",
    "BOTTOM": "bottom",
    "SUPPORT": "utility",
    "UTILITY": "utility",
}

# Trinkets, wards and consumables say nothing about the build.
NON_BUILD_ITEMS = {0, 2003, 2010, 2031, 2033, 2055, 2138, 2139, 2140, 3340, 3363, 3364}


class LoadoutError(Exception):
    """Import failure with a message the player can read."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


async def client_connected() -> bool:
    return await riot_lcu_service.ensure_connection()


def client_position(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return POSITION_ALIASES.get(raw.strip().upper())


def _champion_key(name: str) -> str:
    """'Lee Sin', 'LeeSin' and "Kai'Sa" all compare equal."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _perk_view(perk: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(perk.get("id") or 0),
        "name": perk.get("name") or "",
        "iconPath": perk.get("iconPath") or "",
    }


def _page_view(page: dict[str, Any]) -> Optional[dict[str, Any]]:
    perks = [perk for perk in page.get("perks") or [] if isinstance(perk, dict)]
    if len(perks) != 9:
        return None
    return {
        "position": str(page.get("position") or "").upper() or None,
        "primaryStyleId": int(page.get("primaryPerkStyleId") or 0),
        "subStyleId": int(page.get("secondaryPerkStyleId") or 0),
        "perks": [_perk_view(perk) for perk in perks],
        "summonerSpellIds": [int(spell) for spell in page.get("summonerSpellIds") or [] if isinstance(spell, int)],
        "isDefaultPosition": bool(page.get("isDefaultPosition")),
    }


async def recommended_rune_pages(champion_id: int, position: Optional[str]) -> list[dict[str, Any]]:
    """Client recommendations for the champion; the requested position goes first."""
    lane = client_position(position) or "none"
    payload = await riot_lcu_service.get_json(
        f"/lol-perks/v1/recommended-pages/champion/{champion_id}/position/{lane}/map/{RIFT_MAP_ID}"
    )
    if not isinstance(payload, list):
        return []
    pages: list[dict[str, Any]] = []
    seen: set[tuple[int, ...]] = set()
    for view in (_page_view(page) for page in payload if isinstance(page, dict)):
        if not view:
            continue
        # The client repeats identical pages under different recommendation ids.
        signature = (view["primaryStyleId"], view["subStyleId"], *(perk["id"] for perk in view["perks"]))
        if signature in seen:
            continue
        seen.add(signature)
        pages.append(view)
    wanted = POSITION_ALIASES.get((position or "").upper(), "").upper()
    pages.sort(key=lambda page: (page["position"] != wanted, not page["isDefaultPosition"]))
    return pages


async def history_build(db: AsyncSession, user_id: int, champion_name: str) -> dict[str, Any]:
    """Most frequent finished items in the player's Rift games with this champion."""
    rows = await db.execute(
        select(LocalMatchHistory.champion_name, LocalMatchHistory.items_json)
        .where(
            LocalMatchHistory.user_id == user_id,
            LocalMatchHistory.queue_id.in_(SUMMONERS_RIFT_QUEUES)
            | (LocalMatchHistory.game_mode == "CLASSIC"),
        )
        .order_by(LocalMatchHistory.played_at.desc())
        .limit(HISTORY_SCAN_LIMIT)
    )
    wanted = _champion_key(champion_name)
    counts: Counter[int] = Counter()
    games = 0
    for name, items_json in rows.all():
        if _champion_key(name or "") != wanted:
            continue
        games += 1
        try:
            items = json.loads(items_json or "[]")
        except json.JSONDecodeError:
            continue
        # A repeated item in one game still counts as one vote.
        counts.update({int(item) for item in items if isinstance(item, int) and item not in NON_BUILD_ITEMS})
    item_ids = [item for item, _ in counts.most_common(MAX_BUILD_ITEMS)]
    return {"games": games, "itemIds": item_ids}


async def import_rune_page(
    champion_name: str,
    primary_style_id: int,
    sub_style_id: int,
    perk_ids: list[int],
) -> dict[str, Any]:
    """Replaces the previous LolAnalyzer page with this one and makes it current."""
    pages = await riot_lcu_service.get_json("/lol-perks/v1/pages")
    if pages is None:
        raise LoadoutError(503, "Abre el cliente de League para importar las runas.")

    for page in pages if isinstance(pages, list) else []:
        name = str(page.get("name") or "")
        if name.startswith(PAGE_PREFIX) and page.get("isDeletable") and page.get("id") is not None:
            code, _ = await riot_lcu_service.send("DELETE", f"/lol-perks/v1/pages/{int(page['id'])}")
            if code not in (200, 204):
                raise LoadoutError(502, "El cliente no dejó reemplazar la página anterior de LolAnalyzer.")

    inventory = await riot_lcu_service.get_json("/lol-perks/v1/inventory")
    if isinstance(inventory, dict) and inventory.get("canAddCustomPage") is False:
        raise LoadoutError(409, "No hay espacio para otra página de runas. Borra una en el cliente y vuelve a intentar.")

    body = {
        "name": f"{PAGE_PREFIX} - {champion_name}"[:40],
        "primaryStyleId": primary_style_id,
        "subStyleId": sub_style_id,
        "selectedPerkIds": perk_ids,
        "current": True,
    }
    code, created = await riot_lcu_service.send("POST", "/lol-perks/v1/pages", body)
    if code not in (200, 201):
        logger.info("Rune page import rejected with status %s", code)
        raise LoadoutError(502, "El cliente rechazó la página de runas.")
    return {"name": body["name"], "id": (created or {}).get("id") if isinstance(created, dict) else None}


async def apply_summoner_spells(spell_ids: list[int]) -> bool:
    """Only works during champion select. Returns False outside of it."""
    if len(spell_ids) != 2:
        return False
    code, _ = await riot_lcu_service.send(
        "PATCH",
        "/lol-champ-select/v1/session/my-selection",
        {"spell1Id": spell_ids[0], "spell2Id": spell_ids[1]},
    )
    return code in (200, 204)


async def import_item_set(champion_id: int, champion_name: str, item_ids: list[int], games: int) -> dict[str, Any]:
    """Writes one LolAnalyzer item set for the champion, replacing the earlier one."""
    summoner = await riot_lcu_service.get_current_summoner()
    summoner_id = (summoner or {}).get("summonerId") if isinstance(summoner, dict) else None
    if not summoner_id:
        raise LoadoutError(503, "Abre el cliente de League para importar los objetos.")

    path = f"/lol-item-sets/v1/item-sets/{int(summoner_id)}/sets"
    document = await riot_lcu_service.get_json(path)
    if not isinstance(document, dict):
        raise LoadoutError(502, "No se pudieron leer los conjuntos de objetos del cliente.")

    title = f"{SET_PREFIX}{champion_name}"[:75]
    kept = [
        item_set
        for item_set in document.get("itemSets") or []
        if isinstance(item_set, dict) and item_set.get("title") != title
    ]
    label = f"Tu build habitual ({games} partidas)" if games else "Build sugerida"
    kept.append({
        "uid": str(uuid.uuid4()),
        "title": title,
        "type": "custom",
        "map": "any",
        "mode": "any",
        "associatedMaps": [RIFT_MAP_ID],
        "associatedChampions": [champion_id],
        "preferredItemSlots": [],
        "sortrank": 0,
        "startedFrom": "blank",
        "blocks": [{
            "type": label,
            "items": [{"id": str(item_id), "count": 1} for item_id in item_ids],
            "hideIfSummonerSpell": "",
            "showIfSummonerSpell": "",
        }],
    })
    document["itemSets"] = kept
    code, _ = await riot_lcu_service.send("PUT", path, document)
    if code not in (200, 201, 204):
        logger.info("Item set import rejected with status %s", code)
        raise LoadoutError(502, "El cliente rechazó el conjunto de objetos.")
    return {"title": title, "itemIds": item_ids}
