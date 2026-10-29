import os
from pathlib import Path

import pytest

# Las pruebas nunca deben tocar la base de datos real configurada en .env.
# Las variables de entorno tienen prioridad sobre .env en pydantic-settings,
# y esto corre antes de que cualquier prueba importe la app.
TEST_DB_PATH = Path(__file__).resolve().parent.parent / "test_lol_analyzer.db"
if TEST_DB_PATH.exists():
    TEST_DB_PATH.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["LCU_AUTO_CONNECT"] = "False"


@pytest.fixture(autouse=True)
def offline_champion_catalog(monkeypatch):
    async def fake_catalog(_db):
        from app.services.champion_catalog import bundled_catalog
        return bundled_catalog()

    monkeypatch.setattr("app.api.auth.get_champion_catalog", fake_catalog)
    monkeypatch.setattr("app.api.riot.get_champion_catalog", fake_catalog)
