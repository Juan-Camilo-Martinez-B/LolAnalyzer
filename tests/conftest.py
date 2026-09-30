import pytest


@pytest.fixture(autouse=True)
def offline_champion_catalog(monkeypatch):
    async def fake_catalog(_db):
        from app.services.champion_catalog import bundled_catalog
        return bundled_catalog()

    monkeypatch.setattr("app.api.auth.get_champion_catalog", fake_catalog)
    monkeypatch.setattr("app.api.riot.get_champion_catalog", fake_catalog)
