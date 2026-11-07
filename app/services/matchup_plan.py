"""
Short lane plan for champion select, from OP.GG's matchup data.

Only runs when the lane opponent is known. Groq turns the matchup facts into
three Spanish sentences; without a key the plan is built from the same facts.
Plans are cached per champion, opponent and lane, so a matchup costs one call.
"""

import logging
from typing import Any, Optional

from app.services.groq_coach import groq_coach

logger = logging.getLogger("lol_analyzer.matchup_plan")

PLAN_TOKENS = 160
MAX_TEXT = 220

PLAN_SYSTEM_PROMPT = (
    "Eres coach de League of Legends. Con los datos del matchup, escribe un plan de línea en español. "
    'Responde con JSON {"linea": str, "pelear": str, "evitar": str}. '
    "Cada valor es una frase concreta de máximo 22 palabras. Usa solo los datos dados."
)

STYLE_TEXT = {
    "aggressive": "Juega agresivo en la fase de líneas y presiona desde el nivel 1",
    "even": "Línea pareja: farmea seguro y castiga solo sus errores",
    "passive": "Juega seguro, prioriza el farmeo y escala hacia la fase media",
    "defensive": "Juega seguro, prioriza el farmeo y escala hacia la fase media",
}

_cache: dict[tuple[str, str, str], dict[str, str]] = {}


def _clean(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("*", "").split())
    return text[:MAX_TEXT] if text else None


def plan_prompt(champion: str, opponent: str, lane: str, build: dict[str, Any]) -> str:
    facts = [f"{champion} vs {opponent} en {lane}"]
    if build.get("laneAdvantage"):
        facts.append(f"ventaja de línea: {build['laneAdvantage']}")
    if build.get("soloKillAdvantage"):
        facts.append(f"más asesinatos en solitario: {build['soloKillAdvantage']}")
    if build.get("playStyle"):
        facts.append(f"estilo recomendado: {build['playStyle']}")
    if build.get("skillOrder"):
        facts.append("máximo de habilidades: " + " > ".join(build["skillOrder"]))
    if build.get("tip"):
        facts.append(f"consejo de OP.GG: {build['tip']}")
    return ". ".join(facts) + "."


def rules_plan(champion: str, opponent: str, build: dict[str, Any]) -> dict[str, str]:
    style = STYLE_TEXT.get((build.get("playStyle") or "").lower(), "Farmea seguro y busca ventajas con tu jungla")
    advantage = build.get("laneAdvantage")
    if advantage == champion:
        lane = f"{style}: tienes ventaja de línea contra {opponent}."
    elif advantage == opponent:
        lane = f"{opponent} tiene ventaja de línea: juega seguro y farmea bajo torre si te presiona."
    else:
        lane = f"{style}."
    solo = build.get("soloKillAdvantage")
    fight = (
        f"Busca el asesinato en solitario cuando {opponent} use sus habilidades clave."
        if solo == champion else
        f"Pelea con tu jungla cerca; {opponent} gana más duelos en solitario."
        if solo == opponent else
        "Intercambia cuando tengas tus habilidades listas y él no."
    )
    avoid = f"Evita pelear contra {opponent} con poca vida o sin visión del jungla enemigo."
    return {"lane": lane, "fight": fight, "avoid": avoid}


async def matchup_plan(champion: str, opponent: str, lane: str, build: dict[str, Any]) -> dict[str, str]:
    """{lane, fight, avoid, source}. One Groq call per matchup at most."""
    key = (champion, opponent, lane)
    if key in _cache:
        return _cache[key]

    plan: Optional[dict[str, str]] = None
    try:
        reply = await groq_coach.complete_json(PLAN_SYSTEM_PROMPT, plan_prompt(champion, opponent, lane, build), PLAN_TOKENS)
    except Exception as exc:
        logger.info("Groq matchup plan failed: %s", type(exc).__name__)
        reply = None
    if reply:
        texts = {"lane": _clean(reply.get("linea")), "fight": _clean(reply.get("pelear")), "avoid": _clean(reply.get("evitar"))}
        if all(texts.values()):
            plan = {**{k: str(v) for k, v in texts.items()}, "source": "groq"}
    if plan is None:
        plan = {**rules_plan(champion, opponent, build), "source": "rules"}
        # A rules plan is cheap; only cache Groq answers so a later key is used.
        return plan
    _cache[key] = plan
    return plan
