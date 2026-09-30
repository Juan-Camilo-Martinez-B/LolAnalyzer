"""
Normalization and catalogs for account-recovery answers.
Answers are compared only after normalization and hashing.
"""

import re
import unicodedata

ELO_CANONICAL = {
    "hierro": "hierro",
    "iron": "hierro",
    "bronce": "bronce",
    "bronze": "bronce",
    "plata": "plata",
    "silver": "plata",
    "oro": "oro",
    "gold": "oro",
    "platino": "platino",
    "platinum": "platino",
    "esmeralda": "esmeralda",
    "emerald": "esmeralda",
    "diamante": "diamante",
    "diamond": "diamante",
    "maestro": "maestro",
    "master": "maestro",
    "gran maestro": "gran maestro",
    "granmaestro": "gran maestro",
    "grandmaster": "gran maestro",
    "challenger": "challenger",
}

ELO_OPTIONS = [
    "hierro",
    "bronce",
    "plata",
    "oro",
    "platino",
    "esmeralda",
    "diamante",
    "maestro",
    "gran maestro",
    "challenger",
]

BUNDLED_CHAMPIONS = [
    ("Aatrox", "Aatrox"),
    ("Ahri", "Ahri"),
    ("Akali", "Akali"),
    ("Ashe", "Ashe"),
    ("Blitzcrank", "Blitzcrank"),
    ("Caitlyn", "Caitlyn"),
    ("Darius", "Darius"),
    ("Diana", "Diana"),
    ("Draven", "Draven"),
    ("Ezreal", "Ezreal"),
    ("Fiora", "Fiora"),
    ("Garen", "Garen"),
    ("Graves", "Graves"),
    ("Jinx", "Jinx"),
    ("Kaisa", "Kai'Sa"),
    ("Khazix", "Kha'Zix"),
    ("Leesin", "Lee Sin"),
    ("Leona", "Leona"),
    ("Lux", "Lux"),
    ("MasterYi", "Master Yi"),
    ("MissFortune", "Miss Fortune"),
    ("Nautilus", "Nautilus"),
    ("Pyke", "Pyke"),
    ("Samira", "Samira"),
    ("Senna", "Senna"),
    ("Sett", "Sett"),
    ("Thresh", "Thresh"),
    ("Vayne", "Vayne"),
    ("Yasuo", "Yasuo"),
    ("Yone", "Yone"),
    ("Zed", "Zed"),
]


def normalize_answer(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.strip().lower())
    without_marks = "".join(char for char in folded if not unicodedata.combining(char))
    compact = re.sub(r"[^a-z0-9]+", "", without_marks)
    return compact


def canonical_elo(value: str) -> str | None:
    folded = unicodedata.normalize("NFKD", value.strip().lower())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    plain = re.sub(r"\s+", " ", plain).strip()
    return ELO_CANONICAL.get(plain)


def canonical_champion(value: str, catalog: list[dict[str, str]]) -> str | None:
    needle = normalize_answer(value)
    if not needle:
        return None
    for champion in catalog:
        if needle in {normalize_answer(champion["id"]), normalize_answer(champion["name"])}:
            return normalize_answer(champion["id"])
    return None
