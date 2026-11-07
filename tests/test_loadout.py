"""Rune and item imports against a fake League client. Nothing reaches a real client."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import loadout


class FakeClient:
    """Records writes and answers reads like the client's perks and item-set routes."""

    def __init__(self, pages=None, can_add=True, item_sets=None):
        self.pages = pages or []
        self.can_add = can_add
        self.document = {"accountId": 1, "timestamp": 0, "itemSets": item_sets or []}
        self.calls: list[tuple[str, str, object]] = []

    async def ensure_connection(self):
        return True

    async def get_current_summoner(self):
        return {"summonerId": 77, "puuid": "p"}

    async def get_json(self, path):
        if path == "/lol-perks/v1/pages":
            return self.pages
        if path == "/lol-perks/v1/inventory":
            return {"canAddCustomPage": self.can_add}
        if path.startswith("/lol-item-sets/"):
            return self.document
        if path.startswith("/lol-perks/v1/recommended-pages/"):
            perks = [{"id": 8000 + n, "name": f"Perk {n}", "iconPath": "/x.png"} for n in range(9)]
            return [
                {"position": "TOP", "primaryPerkStyleId": 8400, "secondaryPerkStyleId": 8000,
                 "perks": perks, "summonerSpellIds": [4, 12], "isDefaultPosition": True},
                {"position": "MIDDLE", "primaryPerkStyleId": 8100, "secondaryPerkStyleId": 8300,
                 "perks": perks, "summonerSpellIds": [4, 14], "isDefaultPosition": False},
            ]
        return None

    async def send(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "PUT":
            self.document = payload
        return 200, {"id": 99}


@pytest.fixture
def fake_client(monkeypatch):
    fake = FakeClient(pages=[
        {"id": 1, "name": "Mi pagina", "isDeletable": True},
        {"id": 2, "name": "LolAnalyzer - Ahri", "isDeletable": True},
    ])
    monkeypatch.setattr(loadout, "riot_lcu_service", fake)
    return fake


def test_rune_import_replaces_only_the_lolanalyzer_page(fake_client):
    result = asyncio.run(loadout.import_rune_page("Yasuo", 8000, 8400, list(range(8000, 8009))))
    deletes = [path for method, path, _ in fake_client.calls if method == "DELETE"]
    assert deletes == ["/lol-perks/v1/pages/2"]
    method, path, body = fake_client.calls[-1]
    assert (method, path) == ("POST", "/lol-perks/v1/pages")
    assert body["current"] is True and body["name"] == "LolAnalyzer - Yasuo"
    assert result["name"] == "LolAnalyzer - Yasuo"


def test_rune_import_does_not_delete_player_pages_when_full(monkeypatch):
    fake = FakeClient(pages=[{"id": 1, "name": "Mi pagina", "isDeletable": True}], can_add=False)
    monkeypatch.setattr(loadout, "riot_lcu_service", fake)
    with pytest.raises(loadout.LoadoutError) as error:
        asyncio.run(loadout.import_rune_page("Yasuo", 8000, 8400, list(range(8000, 8009))))
    assert error.value.status_code == 409
    assert fake.calls == []


def test_item_import_is_idempotent_and_keeps_other_sets(monkeypatch):
    other = {"title": "U.GG - Alistar", "uid": "keep"}
    fake = FakeClient(item_sets=[other])
    monkeypatch.setattr(loadout, "riot_lcu_service", fake)
    for _ in range(2):
        asyncio.run(loadout.import_item_set(157, "Yasuo", [3031, 6673], 4))
    titles = [item_set["title"] for item_set in fake.document["itemSets"]]
    assert titles == ["U.GG - Alistar", "LolAnalyzer - Yasuo"]
    mine = fake.document["itemSets"][-1]
    assert mine["associatedChampions"] == [157] and mine["associatedMaps"] == [11]
    assert [item["id"] for item in mine["blocks"][0]["items"]] == ["3031", "6673"]


def test_recommended_pages_put_the_requested_position_first(fake_client):
    pages = asyncio.run(loadout.recommended_rune_pages(157, "MID"))
    assert [page["position"] for page in pages] == ["MIDDLE", "TOP"]
    assert len(pages[0]["perks"]) == 9


def test_recommended_pages_drop_identical_copies(monkeypatch):
    fake = FakeClient()
    original = fake.get_json

    async def doubled(path):
        payload = await original(path)
        return payload + payload if isinstance(payload, list) and "recommended-pages" in path else payload

    fake.get_json = doubled
    monkeypatch.setattr(loadout, "riot_lcu_service", fake)
    assert len(asyncio.run(loadout.recommended_rune_pages(157, "TOP"))) == 2


def _headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"load_{uuid.uuid4().hex[:8]}@example.com",
            "username": "LoadUser",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "favorite_champion": "Ahri",
            "peak_elo": "oro",
            "first_main": "Jinx",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_loadout_routes_need_a_session_and_validate_input(fake_client):
    with TestClient(app) as client:
        assert client.get("/api/champ-select/loadout?champion_id=157&champion_name=Yasuo").status_code == 401
        headers = _headers(client)
        bad = client.post(
            "/api/champ-select/loadout/runes",
            json={"champion_name": "Yasuo", "primary_style_id": 8000, "sub_style_id": 8400, "perk_ids": [1, 2]},
            headers=headers,
        )
        assert bad.status_code == 422
        body = client.get(
            "/api/champ-select/loadout?champion_id=157&champion_name=Yasuo&position=MID",
            headers=headers,
        ).json()
        assert body["runePages"][0]["position"] == "MIDDLE"
        assert body["build"] == {"games": 0, "itemIds": []}
