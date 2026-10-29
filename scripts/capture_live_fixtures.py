"""
Captures a real Live Client or Champion Select payload from the local League
client, anonymizes it and stores it under tests/fixtures.

Usage (from lol-analyzer-backend, with the game or the client open):
    python -m scripts.capture_live_fixtures live practice_tool_10min
    python -m scripts.capture_live_fixtures champ-select custom_vs_bots
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.services.live_game import fetch_live_game
from app.services.riot_lcu import riot_lcu_service
from scripts.fixture_anonymizer import anonymize_payload

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
TARGETS = {
    "live": ("live_client", "la Live Client API (abre una partida, por ejemplo en Practice Tool)"),
    "champ-select": ("champ_select", "la selección de campeón (entra a una personalizada o contra bots)"),
}


async def _read(kind: str) -> dict[str, Any] | None:
    if kind == "live":
        return await fetch_live_game()
    try:
        return await riot_lcu_service.get_champ_select_session()
    finally:
        await riot_lcu_service.close()


async def capture(kind: str, name: str, force: bool) -> int:
    folder, source = TARGETS[kind]
    output = FIXTURES_DIR / folder / f"{name}.json"
    if output.exists() and not force:
        print(f"Ya existe {output}. Usa --force para reemplazarlo.")
        return 1

    payload = await _read(kind)
    if payload is None:
        print(f"No se pudo leer {source}.")
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(anonymize_payload(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Fixture anonimizado guardado en {output}")
    print("Revísalo antes de hacer commit: no debe contener tu Riot ID ni el de otros jugadores.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Captura fixtures anonimizados del cliente de LoL.")
    parser.add_argument("kind", choices=sorted(TARGETS))
    parser.add_argument("name", help="Nombre del archivo, sin extensión (letras, números y guiones bajos).")
    parser.add_argument("--force", action="store_true", help="Reemplaza el fixture si ya existe.")
    args = parser.parse_args()

    if not args.name.replace("_", "").isalnum():
        print("El nombre solo puede tener letras, números y guiones bajos.")
        return 2
    return asyncio.run(capture(args.kind, args.name, args.force))


if __name__ == "__main__":
    sys.exit(main())
