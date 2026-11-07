"""
Meta runes, items and lane matchups from OP.GG's public MCP endpoint.

Two tools are used:
- lol_get_lane_matchup_guide: build against a specific lane opponent (JSON).
- lol_get_champion_analysis: general build when there is no opponent yet.
  It answers in a positional "class" notation that parse_class_notation reads.

Results are normalized to one shape and cached in memory, so champion select
does not hit OP.GG on every poll.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("lol_analyzer.opgg")

PROTOCOL_VERSION = "2025-03-26"
OPGG_POSITIONS = {
    "TOP": "top",
    "JUNGLE": "jungle",
    "MID": "mid",
    "MIDDLE": "mid",
    "ADC": "adc",
    "BOTTOM": "adc",
    "SUPPORT": "support",
    "UTILITY": "support",
}
ANALYSIS_FIELDS = [
    "data.runes.{primary_page_id,primary_rune_ids[],secondary_page_id,secondary_rune_ids[],stat_mod_ids[],pick_rate,play,win}",
    "data.summoner_spells.{ids[],pick_rate}",
    "data.starter_items.{ids[],pick_rate}",
    "data.boots.{ids[],pick_rate}",
    "data.core_items.{ids[],pick_rate}",
    "data.fourth_items[].{ids[],pick_rate}",
    "data.fifth_items[].{ids[],pick_rate}",
    "data.last_items[].{ids[],pick_rate}",
    "data.skill_masteries.{ids[]}",
    "data.summary.average_stats.{win_rate,pick_rate}",
]


class OpggUnavailable(Exception):
    """OP.GG did not answer or did not know the champion."""


def opgg_champion_name(display_name: str) -> str:
    """'Lee Sin' -> LEE_SIN, "Kai'Sa" -> KAISA, 'Nunu & Willump' -> NUNU_WILLUMP."""
    cleaned = display_name.upper().replace("'", "").replace(".", "")
    return re.sub(r"[^A-Z0-9]+", "_", cleaned).strip("_")


def opgg_position(position: Optional[str]) -> Optional[str]:
    if not position:
        return None
    return OPGG_POSITIONS.get(position.strip().upper())


# ---------------------------------------------------------------------------
# Positional class notation, e.g.
#   class Data: core_items,runes
#   class CoreItems: ids,pick_rate
#   LolGetChampionAnalysis(Data(CoreItems([6673,3031],0.37),Runes(...)))
# Every Name(...) becomes a dict using the declared field order.
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r'\s*(?:(?P<num>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)|(?P<str>"(?:\\.|[^"\\])*")|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<sym>[()\[\],]))')


def _tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        match = _TOKEN.match(text, position)
        if not match or match.end() == position:
            raise ValueError(f"Unexpected character at {position}")
        kind = match.lastgroup or "sym"
        tokens.append((kind, match.group(kind)))
        position = match.end()
    return tokens


def parse_class_notation(text: str) -> Any:
    """Turns OP.GG's class notation into plain dicts and lists."""
    header, _, body = text.partition("\n\n")
    fields: dict[str, list[str]] = {}
    for line in header.splitlines():
        if line.startswith("class ") and ":" in line:
            name, _, names = line[6:].partition(":")
            fields[name.strip()] = [item.strip() for item in names.split(",") if item.strip()]

    tokens = _tokens(body.strip())
    index = 0

    def peek() -> tuple[str, str]:
        return tokens[index] if index < len(tokens) else ("eof", "")

    def take(expected: Optional[str] = None) -> tuple[str, str]:
        nonlocal index
        token = peek()
        if expected is not None and token[1] != expected:
            raise ValueError(f"Expected {expected!r}, got {token[1]!r}")
        index += 1
        return token

    def values_until(closing: str) -> list[Any]:
        items: list[Any] = []
        while peek()[1] != closing:
            items.append(value())
            if peek()[1] == ",":
                take(",")
        take(closing)
        return items

    def value() -> Any:
        kind, raw = take()
        if kind == "num":
            return float(raw) if any(c in raw for c in ".eE") else int(raw)
        if kind == "str":
            return json.loads(raw)
        if raw == "[":
            return values_until("]")
        if kind == "name":
            if peek()[1] == "(":
                take("(")
                args = values_until(")")
                names = fields.get(raw, [])
                return {names[i] if i < len(names) else f"_{i}": arg for i, arg in enumerate(args)}
            return {"None": None, "True": True, "False": False}.get(raw, raw)
        raise ValueError(f"Unexpected token {raw!r}")

    root = value()
    return root


def _first(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), None)
    return value if isinstance(value, dict) else None


def _ids(entry: Any) -> list[int]:
    entry = _first(entry)
    return [int(item) for item in (entry or {}).get("ids") or [] if isinstance(item, (int, float))]


def _unique(items: list[int], exclude: set[int]) -> list[int]:
    seen = set(exclude)
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_build(data: dict[str, Any]) -> dict[str, Any]:
    """OP.GG data (either tool) -> runes, spells, item blocks and tips."""
    runes = _first(data.get("runes")) or {}
    primary = [int(x) for x in runes.get("primary_rune_ids") or []]
    secondary = [int(x) for x in runes.get("secondary_rune_ids") or []]
    shards = [int(x) for x in runes.get("stat_mod_ids") or []]
    rune_page = None
    if len(primary) == 4 and len(secondary) == 2 and len(shards) == 3:
        rune_page = {
            "primaryStyleId": int(runes.get("primary_page_id") or 0),
            "subStyleId": int(runes.get("secondary_page_id") or 0),
            "perkIds": primary + secondary + shards,
            "pickRate": float(runes.get("pick_rate") or 0),
        }

    starter = _ids(data.get("starter_items"))
    boots = _ids(data.get("boots"))
    core = _ids(data.get("core_items"))
    later: list[int] = []
    for key in ("fourth_items", "fifth_items", "last_items"):
        for entry in data.get(key) or []:
            later.extend(_ids(entry))
    situational = _unique(later, set(core) | set(boots))[:8]

    # Starter items keep repeats (two potions); the item set turns them into a count.
    blocks = []
    if starter:
        blocks.append({"kind": "starter", "itemIds": starter})
    if core or boots:
        blocks.append({"kind": "core", "itemIds": _unique(core + boots, set())})
    if situational:
        blocks.append({"kind": "situational", "itemIds": situational})

    skills = data.get("skill_masteries")
    skill_order = [str(x) for x in (_first(skills) or {}).get("ids") or []]
    if not skill_order:
        skill_order = [str(x) for x in (_first(data.get("skills")) or {}).get("order") or []][:3]

    tip = data.get("opponent_champion_tip") if isinstance(data.get("opponent_champion_tip"), str) else None
    return {
        "runePage": rune_page,
        "summonerSpellIds": _ids(data.get("summoner_spells"))[:2],
        "itemBlocks": blocks,
        "skillOrder": skill_order,
        "tip": tip,
        "laneAdvantage": data.get("lane_advantage_champion") if isinstance(data.get("lane_advantage_champion"), str) else None,
    }


class OpggMetaClient:
    """Small MCP client: one session, re-opened when OP.GG drops it."""

    def __init__(self) -> None:
        self._session_id: Optional[str] = None
        self._lock = asyncio.Lock()
        self._cache: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}
        self._next_id = 1

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        text = response.text
        if "data:" in text[:64]:
            text = "\n".join(line[5:].strip() for line in text.splitlines() if line.startswith("data:"))
        return json.loads(text) if text.strip() else None

    async def _open(self, client: httpx.AsyncClient) -> None:
        self._session_id = None
        response = await client.post(settings.OPGG_MCP_URL, headers=self._headers(), json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "lolanalyzer", "version": "1.0"},
            },
        })
        response.raise_for_status()
        self._session_id = response.headers.get("mcp-session-id")
        await client.post(settings.OPGG_MCP_URL, headers=self._headers(),
                          json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def call(self, tool: str, arguments: dict[str, Any]) -> str:
        """Returns the tool's text content. Raises OpggUnavailable on any failure."""
        async with self._lock:
            try:
                async with httpx.AsyncClient(timeout=settings.OPGG_TIMEOUT_SECONDS) as client:
                    for attempt in range(2):
                        if self._session_id is None or attempt == 1:
                            await self._open(client)
                        self._next_id += 1
                        response = await client.post(settings.OPGG_MCP_URL, headers=self._headers(), json={
                            "jsonrpc": "2.0",
                            "id": self._next_id,
                            "method": "tools/call",
                            "params": {"name": tool, "arguments": arguments},
                        })
                        if response.status_code in (400, 404) and attempt == 0:
                            continue  # Session expired: open a new one once.
                        response.raise_for_status()
                        body = self._decode(response) or {}
                        if body.get("error"):
                            raise OpggUnavailable(str(body["error"].get("message") or "OP.GG error"))
                        content = (body.get("result") or {}).get("content") or []
                        text = next((item.get("text") for item in content if item.get("type") == "text"), None)
                        if not text:
                            raise OpggUnavailable("OP.GG returned no data")
                        return text
            except OpggUnavailable:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                logger.info("OP.GG request failed: %s", type(exc).__name__)
                raise OpggUnavailable("OP.GG is unavailable") from exc
        raise OpggUnavailable("OP.GG is unavailable")

    async def build(self, champion: str, position: str, opponent: Optional[str] = None) -> dict[str, Any]:
        """Meta build for champion/position, against an opponent when one is known."""
        key = (champion, position, opponent or "")
        cached = self._cache.get(key)
        ttl = settings.OPGG_CACHE_MINUTES * 60
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1]

        if opponent:
            text = await self.call("lol_get_lane_matchup_guide", {
                "position": position,
                "my_champion": champion,
                "opponent_champion": opponent,
            })
            payload = json.loads(text)
            data = payload.get("data") or {}
        else:
            text = await self.call("lol_get_champion_analysis", {
                "game_mode": "ranked",
                "champion": champion,
                "position": position,
                "desired_output_fields": ANALYSIS_FIELDS,
            })
            parsed = parse_class_notation(text)
            data = (parsed or {}).get("data") or {}

        result = normalize_build(data)
        if not result["runePage"] and not result["itemBlocks"]:
            raise OpggUnavailable("OP.GG has no build for that champion and lane")
        self._cache[key] = (time.monotonic(), result)
        return result


opgg_meta_client = OpggMetaClient()
