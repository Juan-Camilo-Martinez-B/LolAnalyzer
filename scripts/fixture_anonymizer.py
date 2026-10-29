"""
Replaces Riot identities in captured Live Client and LCU payloads so they can be
committed as test fixtures. Every player gets a stable alias (Player1, Player2...)
that is reused wherever the same identity appears, including kill events.
"""

import copy
from typing import Any

NAME_KEYS = {"summonerName", "riotId", "riotIdGameName", "gameName", "displayName"}
TAG_KEYS = {"riotIdTagLine", "tagLine"}
ID_KEYS = {
    "puuid",
    "summonerId",
    "accountId",
    "playerId",
    "obfuscatedPuuid",
    "obfuscatedSummonerId",
}
ANON_TAG = "ANON"


def _base_name(value: str) -> str:
    return value.split("#", 1)[0].strip()


class _AliasBook:
    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.ids: dict[tuple[str, Any], Any] = {}

    def register_name(self, value: str) -> None:
        base = _base_name(value)
        if base and base not in self.names:
            self.names[base] = f"Player{len(self.names) + 1}"

    def alias_for_text(self, value: str) -> str:
        base = _base_name(value)
        alias = self.names.get(base)
        if alias is None:
            return value
        return f"{alias}#{ANON_TAG}" if "#" in value else alias

    def alias_for_id(self, key: str, value: Any) -> Any:
        # 0 y cadenas vacias significan "sin jugador" (bots o huecos) y se conservan.
        if value in (0, "", None):
            return value
        token = (key, value)
        if token not in self.ids:
            index = sum(1 for known_key, _ in self.ids if known_key == key) + 1
            self.ids[token] = index if isinstance(value, int) else f"anon-{key}-{index}"
        return self.ids[token]


def _collect(node: Any, book: _AliasBook) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in NAME_KEYS and isinstance(value, str):
                book.register_name(value)
            else:
                _collect(value, book)
    elif isinstance(node, list):
        for item in node:
            _collect(item, book)


def _replace(node: Any, book: _AliasBook, key: str | None = None) -> Any:
    if isinstance(node, dict):
        return {k: _replace(v, book, k) for k, v in node.items()}
    if isinstance(node, list):
        return [_replace(item, book, key) for item in node]
    if key in ID_KEYS:
        return book.alias_for_id(key, node)
    if key in TAG_KEYS and isinstance(node, str) and node:
        return ANON_TAG
    if isinstance(node, str):
        return book.alias_for_text(node)
    return node


def anonymize_payload(payload: Any) -> Any:
    """Returns a deep copy of payload with every player identity replaced by an alias."""
    book = _AliasBook()
    snapshot = copy.deepcopy(payload)
    _collect(snapshot, book)
    return _replace(snapshot, book)
