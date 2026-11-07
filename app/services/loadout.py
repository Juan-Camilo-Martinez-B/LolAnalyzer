"""
Runes and item sets for champion select, written to the local League client.

The build comes from OP.GG meta data: against the lane opponent when champion
select already shows them, otherwise the champion's general build for that
lane (practice tool, blind pick or before the enemy picks). When OP.GG is not
available, runes fall back to the client's own recommendations.

Imports replace the page or set LolAnalyzer wrote before, so repeating an
import never piles up duplicates and never touches the player's own pages.
"""

from collections import Counter
import logging
import uuid
from typing import Any, Optional

from app.services.opgg_meta import OpggUnavailable, opgg_champion_name, opgg_meta_client, opgg_position
from app.services.riot_lcu import riot_lcu_service

logger = logging.getLogger("lol_analyzer.loadout")

PAGE_PREFIX = "LolAnalyzer"
SET_PREFIX = "LolAnalyzer - "
RIFT_MAP_ID = 11

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

BLOCK_TITLES = {
    "starter": "Inicio",
    "core": "Build principal",
    "situational": "Situacionales",
}

# Static client data does not change during a session.
_static_cache: dict[str, Any] = {}


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


def _canonical(position: Optional[str]) -> Optional[str]:
    lane = client_position(position)
    return lane.upper() if lane else None


async def _static(path: str) -> Any:
    if path in _static_cache:
        return _static_cache[path]
    payload = await riot_lcu_service.get_json(path)
    if payload:
        _static_cache[path] = payload
    return payload


async def champion_names() -> dict[int, str]:
    """Champion id -> display name ('Wukong', "Kai'Sa"), read from the client."""
    payload = await _static("/lol-game-data/assets/v1/champion-summary.json")
    names: dict[int, str] = {}
    for champion in payload if isinstance(payload, list) else []:
        champion_id = champion.get("id") if isinstance(champion, dict) else None
        if isinstance(champion_id, int) and champion_id > 0 and champion.get("name"):
            names[champion_id] = str(champion["name"])
    return names


async def perk_catalog() -> dict[int, dict[str, Any]]:
    """Rune id -> name and icon, so OP.GG pages render like the client's."""
    payload = await _static("/lol-perks/v1/perks")
    catalog: dict[int, dict[str, Any]] = {}
    for perk in payload if isinstance(payload, list) else []:
        if isinstance(perk, dict) and isinstance(perk.get("id"), int):
            catalog[perk["id"]] = {"id": perk["id"], "name": perk.get("name") or "", "iconPath": perk.get("iconPath") or ""}
    return catalog


def _page_view(page: dict[str, Any]) -> Optional[dict[str, Any]]:
    perks = [perk for perk in page.get("perks") or [] if isinstance(perk, dict)]
    if len(perks) != 9:
        return None
    return {
        "source": "client",
        "label": "Recomendación del cliente",
        "position": str(page.get("position") or "").upper() or None,
        "primaryStyleId": int(page.get("primaryPerkStyleId") or 0),
        "subStyleId": int(page.get("secondaryPerkStyleId") or 0),
        "perks": [
            {"id": int(perk.get("id") or 0), "name": perk.get("name") or "", "iconPath": perk.get("iconPath") or ""}
            for perk in perks
        ],
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
    wanted = _canonical(position) or ""
    pages.sort(key=lambda page: (page["position"] != wanted, not page["isDefaultPosition"]))
    return pages


async def default_position(champion_id: int) -> Optional[str]:
    """The lane the client recommends first for a champion (TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY)."""
    pages = await recommended_rune_pages(champion_id, None)
    default = next((page for page in pages if page["isDefaultPosition"]), pages[0] if pages else None)
    return default["position"] if default else None


async def lane_opponent(my_lane: Optional[str]) -> Optional[int]:
    """
    Enemy champion that plays the same lane, read from the live champion select.
    Enemy lanes are not public, so each enemy pick is placed on its usual lane.
    """
    wanted = _canonical(my_lane)
    if not wanted:
        return None
    session = await riot_lcu_service.get_champ_select_session()
    if not isinstance(session, dict):
        return None
    for enemy in session.get("theirTeam") or []:
        champion_id = int(enemy.get("championId") or 0) if isinstance(enemy, dict) else 0
        if champion_id <= 0:
            continue
        lane = _canonical(enemy.get("assignedPosition")) or await default_position(champion_id)
        if lane == wanted:
            return champion_id
    return None


async def meta_loadout(
    champion_id: int,
    position: Optional[str],
    opponent_id: Optional[int] = None,
) -> dict[str, Any]:
    """
    Meta build from OP.GG plus the client's pages as alternatives.
    Without a known lane, the champion's usual lane is used.
    """
    names = await champion_names()
    champion_name = names.get(champion_id)
    lane = _canonical(position) or await default_position(champion_id)
    if opponent_id is None:
        opponent_id = await lane_opponent(lane)
    opponent_name = names.get(opponent_id) if opponent_id else None

    client_pages = await recommended_rune_pages(champion_id, lane)
    result: dict[str, Any] = {
        "championId": champion_id,
        "championName": champion_name,
        "position": lane,
        "opponent": {"id": opponent_id, "name": opponent_name} if opponent_name else None,
        "source": "client",
        "runePages": client_pages,
        "itemBlocks": [],
        "summonerSpellIds": client_pages[0]["summonerSpellIds"] if client_pages else [],
        "skillOrder": [],
        "tip": None,
        "notice": None,
    }

    opgg_lane = opgg_position(lane)
    if not champion_name or not opgg_lane:
        result["notice"] = "No se pudo saber la línea; se usan las recomendaciones del cliente."
        return result

    try:
        build = await opgg_meta_client.build(
            opgg_champion_name(champion_name),
            opgg_lane,
            opgg_champion_name(opponent_name) if opponent_name else None,
        )
    except OpggUnavailable as exc:
        logger.info("OP.GG build unavailable: %s", exc)
        result["notice"] = "OP.GG no respondió; se usan las recomendaciones del cliente."
        return result

    catalog = await perk_catalog()
    if build["runePage"]:
        page = build["runePage"]
        meta_page = {
            "source": "opgg",
            "label": f"Meta vs {opponent_name}" if opponent_name else "Meta general",
            "position": lane,
            "primaryStyleId": page["primaryStyleId"],
            "subStyleId": page["subStyleId"],
            "perks": [catalog.get(perk_id, {"id": perk_id, "name": "", "iconPath": ""}) for perk_id in page["perkIds"]],
            "summonerSpellIds": build["summonerSpellIds"],
            "isDefaultPosition": True,
            "pickRate": page.get("pickRate"),
        }
        result["runePages"] = [meta_page, *client_pages]

    vs = f" vs {opponent_name}" if opponent_name else ""
    result.update({
        "source": "opgg",
        "itemBlocks": [
            {
                "title": BLOCK_TITLES.get(block["kind"], "Objetos") + ("" if block["kind"] == "situational" else vs),
                "itemIds": block["itemIds"],
            }
            for block in build["itemBlocks"]
        ],
        "summonerSpellIds": build["summonerSpellIds"] or result["summonerSpellIds"],
        "skillOrder": build["skillOrder"],
        "tip": build["tip"],
    })
    return result


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


def _block_items(item_ids: list[int]) -> list[dict[str, Any]]:
    """Repeated ids (two potions) become one entry with a count, in first-seen order."""
    counts = Counter(item_ids)
    return [{"id": str(item_id), "count": counts[item_id]} for item_id in dict.fromkeys(item_ids)]


async def import_item_set(champion_id: int, champion_name: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
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
    new_blocks = [
        {
            "type": str(block["title"])[:60],
            "items": _block_items(block["itemIds"]),
            "hideIfSummonerSpell": "",
            "showIfSummonerSpell": "",
        }
        for block in blocks
        if block.get("itemIds")
    ]
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
        "blocks": new_blocks,
    })
    document["itemSets"] = kept
    code, _ = await riot_lcu_service.send("PUT", path, document)
    if code not in (200, 201, 204):
        logger.info("Item set import rejected with status %s", code)
        raise LoadoutError(502, "El cliente rechazó el conjunto de objetos.")
    return {"title": title, "blocks": len(new_blocks)}
