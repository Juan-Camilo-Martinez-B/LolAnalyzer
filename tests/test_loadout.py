"""Meta runes and items against a fake League client and a fake OP.GG. Nothing reaches the network."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import loadout, opgg_meta
from app.services.opgg_meta import normalize_build, opgg_champion_name, parse_class_notation

ANALYSIS_SAMPLE = """class LolGetChampionAnalysis: data
class Data: summary,core_items,boots,starter_items,last_items,summoner_spells,runes,skill_masteries
class Summary: average_stats
class AverageStats: win_rate,pick_rate
class CoreItems: ids,pick_rate
class Runes: primary_page_id,primary_rune_ids,secondary_page_id,secondary_rune_ids,stat_mod_ids,play,win,pick_rate
class SkillMasteries: ids

LolGetChampionAnalysis(Data(Summary(AverageStats(0.49,0.15)),CoreItems([6673,3031,6333],0.37),CoreItems([3006],0.96),CoreItems([1086,2003,2003],0.73),[CoreItems([6673],0.88),CoreItems([3031],0.64),CoreItems([3153],0.37)],CoreItems([3,4],0.43),Runes(8000,[8008,9101,9104,8299],8400,[8444,8451],[5005,5008,5001],16081,7759,0.39),SkillMasteries(["Q","E","W"])))"""

PERKS = [{"id": 8000 + n, "name": f"Perk {n}", "iconPath": "/x.png"} for n in range(9)]


class FakeClient:
    """Answers reads like the client's perks, item-set, game-data and champ select routes."""

    def __init__(self, pages=None, can_add=True, item_sets=None, enemies=None):
        self.pages = pages or []
        self.can_add = can_add
        self.document = {"accountId": 1, "timestamp": 0, "itemSets": item_sets or []}
        self.enemies = enemies or []
        self.calls: list[tuple[str, str, object]] = []

    async def ensure_connection(self):
        return True

    async def get_current_summoner(self):
        return {"summonerId": 77, "puuid": "p"}

    async def get_champ_select_session(self):
        return {"theirTeam": [{"championId": champion_id, "assignedPosition": ""} for champion_id in self.enemies]}

    async def get_json(self, path):
        if path == "/lol-perks/v1/pages":
            return self.pages
        if path == "/lol-perks/v1/inventory":
            return {"canAddCustomPage": self.can_add}
        if path == "/lol-perks/v1/perks":
            return [{"id": 8008, "name": "Lethal Tempo", "iconPath": "/lt.png"}]
        if path == "/lol-game-data/assets/v1/champion-summary.json":
            return [{"id": 777, "name": "Yone"}, {"id": 103, "name": "Ahri"}, {"id": 64, "name": "Lee Sin"}]
        if path.startswith("/lol-item-sets/"):
            return self.document
        if path.startswith("/lol-perks/v1/recommended-pages/champion/"):
            champion_id = int(path.split("/")[5])
            lane = {777: "MIDDLE", 103: "MIDDLE", 64: "JUNGLE"}.get(champion_id, "TOP")
            return [
                {"position": lane, "primaryPerkStyleId": 8000, "secondaryPerkStyleId": 8400,
                 "perks": PERKS, "summonerSpellIds": [4, 12], "isDefaultPosition": True},
                {"position": lane, "primaryPerkStyleId": 8000, "secondaryPerkStyleId": 8400,
                 "perks": PERKS, "summonerSpellIds": [4, 12], "isDefaultPosition": True},
            ]
        return None

    async def send(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "PUT":
            self.document = payload
        return 200, {"id": 99}


class FakeOpgg:
    def __init__(self, fail=False):
        self.fail = fail
        self.requests: list[tuple] = []

    async def build(self, champion, position, opponent=None):
        self.requests.append((champion, position, opponent))
        if self.fail:
            raise opgg_meta.OpggUnavailable("down")
        return normalize_build(parse_class_notation(ANALYSIS_SAMPLE)["data"])


@pytest.fixture(autouse=True)
def fresh_static_cache():
    loadout._static_cache.clear()
    yield
    loadout._static_cache.clear()


@pytest.fixture
def fake_client(monkeypatch):
    fake = FakeClient(pages=[
        {"id": 1, "name": "Mi pagina", "isDeletable": True},
        {"id": 2, "name": "LolAnalyzer - Ahri", "isDeletable": True},
    ])
    monkeypatch.setattr(loadout, "riot_lcu_service", fake)
    return fake


def test_class_notation_becomes_named_fields():
    data = parse_class_notation(ANALYSIS_SAMPLE)["data"]
    assert data["runes"]["primary_rune_ids"] == [8008, 9101, 9104, 8299]
    assert data["starter_items"]["ids"] == [1086, 2003, 2003]
    assert [entry["ids"] for entry in data["last_items"]] == [[6673], [3031], [3153]]
    assert data["skill_masteries"]["ids"] == ["Q", "E", "W"]


def test_build_keeps_nine_runes_and_separates_situational_items():
    build = normalize_build(parse_class_notation(ANALYSIS_SAMPLE)["data"])
    assert build["runePage"]["perkIds"] == [8008, 9101, 9104, 8299, 8444, 8451, 5005, 5008, 5001]
    assert build["summonerSpellIds"] == [3, 4]
    kinds = {block["kind"]: block["itemIds"] for block in build["itemBlocks"]}
    assert kinds["starter"] == [1086, 2003, 2003]
    assert kinds["core"] == [6673, 3031, 6333, 3006]
    assert kinds["situational"] == [3153]


def test_opgg_names():
    assert opgg_champion_name("Lee Sin") == "LEE_SIN"
    assert opgg_champion_name("Kai'Sa") == "KAISA"
    assert opgg_champion_name("Nunu & Willump") == "NUNU_WILLUMP"
    assert opgg_champion_name("Dr. Mundo") == "DR_MUNDO"


def test_meta_loadout_targets_the_lane_opponent(monkeypatch):
    fake = FakeClient(enemies=[64, 103])
    opgg = FakeOpgg()
    monkeypatch.setattr(loadout, "riot_lcu_service", fake)
    monkeypatch.setattr(loadout, "opgg_meta_client", opgg)
    body = asyncio.run(loadout.meta_loadout(777, "MID"))
    assert opgg.requests == [("YONE", "mid", "AHRI")]
    assert body["opponent"] == {"id": 103, "name": "Ahri"}
    assert body["runePages"][0]["label"] == "Meta vs Ahri"
    assert body["runePages"][0]["perks"][0]["name"] == "Lethal Tempo"
    assert body["itemBlocks"][0]["title"] == "Inicio vs Ahri"
    # Identical client pages are shown once, after the meta page.
    assert len(body["runePages"]) == 2


def test_meta_loadout_without_opponent_uses_the_usual_lane(monkeypatch):
    opgg = FakeOpgg()
    monkeypatch.setattr(loadout, "riot_lcu_service", FakeClient())
    monkeypatch.setattr(loadout, "opgg_meta_client", opgg)
    body = asyncio.run(loadout.meta_loadout(64, None))
    assert opgg.requests == [("LEE_SIN", "jungle", None)]
    assert body["position"] == "JUNGLE" and body["opponent"] is None
    assert body["runePages"][0]["label"] == "Meta general"


def test_meta_loadout_falls_back_to_client_pages(monkeypatch):
    monkeypatch.setattr(loadout, "riot_lcu_service", FakeClient())
    monkeypatch.setattr(loadout, "opgg_meta_client", FakeOpgg(fail=True))
    body = asyncio.run(loadout.meta_loadout(777, "MID"))
    assert body["source"] == "client"
    assert body["runePages"][0]["source"] == "client"
    assert body["itemBlocks"] == []
    assert "OP.GG" in body["notice"]


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


def test_item_import_is_idempotent_counts_repeats_and_keeps_other_sets(monkeypatch):
    other = {"title": "U.GG - Alistar", "uid": "keep"}
    fake = FakeClient(item_sets=[other])
    monkeypatch.setattr(loadout, "riot_lcu_service", fake)
    blocks = [{"title": "Inicio", "itemIds": [1086, 2003, 2003]}, {"title": "Build principal", "itemIds": [6673, 3031]}]
    for _ in range(2):
        asyncio.run(loadout.import_item_set(777, "Yone", blocks))
    titles = [item_set["title"] for item_set in fake.document["itemSets"]]
    assert titles == ["U.GG - Alistar", "LolAnalyzer - Yone"]
    mine = fake.document["itemSets"][-1]
    assert mine["associatedChampions"] == [777] and mine["associatedMaps"] == [11]
    assert mine["blocks"][0]["items"] == [{"id": "1086", "count": 1}, {"id": "2003", "count": 2}]
    assert mine["blocks"][1]["type"] == "Build principal"


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


def test_loadout_routes_need_a_session_and_validate_input(fake_client, monkeypatch):
    monkeypatch.setattr(loadout, "opgg_meta_client", FakeOpgg())
    with TestClient(app) as client:
        assert client.get("/api/champ-select/loadout?champion_id=777").status_code == 401
        headers = _headers(client)
        bad = client.post(
            "/api/champ-select/loadout/runes",
            json={"champion_name": "Yone", "primary_style_id": 8000, "sub_style_id": 8400, "perk_ids": [1, 2]},
            headers=headers,
        )
        assert bad.status_code == 422
        bad_items = client.post(
            "/api/champ-select/loadout/items",
            json={"champion_id": 777, "champion_name": "Yone", "blocks": []},
            headers=headers,
        )
        assert bad_items.status_code == 422
        body = client.get("/api/champ-select/loadout?champion_id=777&position=MID", headers=headers).json()
        assert body["clientConnected"] is True
        assert body["runePages"][0]["source"] == "opgg"
