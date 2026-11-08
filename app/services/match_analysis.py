"""
Post-match analysis from the final match summary (not the live feed).

The numbers are computed here against simple references per role. Groq only
writes three short sentences from one compact line of those numbers; without a
key, or if it fails, the same three sentences are built from rules. Each game
is analysed once and stored in match_analyses, so it is never billed twice.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LocalMatchHistory, MatchAnalysis
from app.services.groq_coach import groq_coach
from app.services.lcu_match_map import SUMMONERS_RIFT_QUEUES

logger = logging.getLogger("lol_analyzer.match_analysis")

ROLE_ALIASES = {"MIDDLE": "MID", "BOTTOM": "ADC", "UTILITY": "SUPPORT", "DUO_CARRY": "ADC", "DUO_SUPPORT": "SUPPORT"}
CS_TARGET = {"TOP": 7.5, "JUNGLE": 6.0, "MID": 8.0, "ADC": 8.5}
VISION_TARGET = {"SUPPORT": 2.0, "JUNGLE": 1.1}
DAMAGE_TARGET = {"TOP": 650, "JUNGLE": 500, "MID": 750, "ADC": 750, "SUPPORT": 300}
GOLD_TARGET = {"TOP": 400, "JUNGLE": 380, "MID": 420, "ADC": 430, "SUPPORT": 260}
DEATHS_PER_10_TARGET = 3.0
KDA_TARGET = 3.0
MIN_DURATION_SECONDS = 5 * 60
RECENT_HOURS = 6
MAX_TEXT = 300
GROQ_ANALYSIS_TOKENS = 220

ANALYSIS_SYSTEM_PROMPT = (
    "Eres coach de League of Legends. Analiza solo las métricas dadas, sin inventar datos. "
    'Responde en español con JSON {"bien": str, "mejorar": str, "foco": str}. '
    "Cada valor es una frase concreta de máximo 25 palabras que cite números del texto."
)

FOCUS_BY_METRIC = {
    "cs": "Practica el último golpe: apunta a {target} CS/min en los primeros 15 minutos.",
    "vision": "Compra un guardián de control en cada vuelta a base y usa el trinket al salir de línea.",
    "deaths": "Antes de avanzar, mira el minimapa: sin visión del jungla enemigo, juega cerca de tu equipo.",
    "damage": "Busca intercambios cuando tus habilidades estén listas y castiga cuando el rival falle las suyas.",
    "gold": "Junta oleadas antes de volver a base y no dejes súbditos sin tomar entre peleas.",
    "kda": "Elige peleas con ventaja numérica y retírate cuando tu equipo no pueda seguirte.",
}


@dataclass
class Metric:
    key: str
    label: str
    value: float
    target: float
    unit: str
    higher_is_better: bool
    verdict: str  # good, ok or bad


def normalize_role(role: Optional[str]) -> str:
    raw = (role or "").strip().upper()
    return ROLE_ALIASES.get(raw, raw or "UNKNOWN")


def _verdict(value: float, target: float, higher_is_better: bool) -> str:
    if target <= 0:
        return "ok"
    ratio = value / target if higher_is_better else (target / value if value > 0 else 2.0)
    if ratio >= 1.0:
        return "good"
    return "ok" if ratio >= 0.85 else "bad"


def compute_metrics(match: LocalMatchHistory) -> list[Metric]:
    """Per-minute numbers of one game against the references of its role."""
    role = normalize_role(match.role)
    minutes = max(1.0, match.duration_seconds / 60)
    metrics: list[Metric] = []

    def add(key: str, label: str, value: float, target: float, unit: str, higher: bool = True) -> None:
        metrics.append(Metric(key, label, round(value, 2), target, unit, higher, _verdict(value, target, higher)))

    if role in CS_TARGET:
        add("cs", "Farmeo", match.cs / minutes, CS_TARGET[role], "CS/min")
    add("vision", "Visión", match.vision_score / minutes, VISION_TARGET.get(role, 0.8), "visión/min")
    add("deaths", "Muertes", match.deaths / (minutes / 10), DEATHS_PER_10_TARGET, "muertes cada 10 min", higher=False)
    add("kda", "KDA", (match.kills + match.assists) / max(1, match.deaths), KDA_TARGET, "KDA")
    if role in DAMAGE_TARGET and match.damage_dealt > 0:
        add("damage", "Daño a campeones", match.damage_dealt / minutes, DAMAGE_TARGET[role], "daño/min")
    if role in GOLD_TARGET and match.gold_earned > 0:
        add("gold", "Oro", match.gold_earned / minutes, GOLD_TARGET[role], "oro/min")
    return metrics


def _fmt(value: float) -> str:
    return f"{value:.1f}" if value < 100 else f"{value:.0f}"


def _describe(metric: Metric) -> str:
    return f"{metric.label}: {_fmt(metric.value)} {metric.unit} (meta {_fmt(metric.target)})"


def rules_analysis(metrics: list[Metric]) -> dict[str, str]:
    """The same three sentences Groq writes, built without a language model."""
    good = [m for m in metrics if m.verdict == "good"]
    bad = sorted(
        (m for m in metrics if m.verdict == "bad"),
        key=lambda m: (m.value / m.target) if m.higher_is_better else (m.target / max(m.value, 0.01)),
    )
    ok = [m for m in metrics if m.verdict == "ok"]

    strengths = (
        "Bien en " + "; ".join(_describe(m) for m in good[:2]) + "."
        if good else "Ninguna métrica superó la referencia del rol; la más cercana fue "
        + (_describe(ok[0]) if ok else _describe(metrics[0])) + "."
    )
    weak = bad[:2] or ok[:1]
    improvements = (
        "A mejorar: " + "; ".join(_describe(m) for m in weak) + "."
        if weak else "Todas las métricas quedaron en la referencia del rol o por encima."
    )
    worst = weak[0] if weak else None
    focus = (
        FOCUS_BY_METRIC.get(worst.key, "Repite lo que funcionó y revisa tus muertes.").format(target=_fmt(worst.target))
        if worst else "Mantén el ritmo y prueba a cerrar antes la partida con objetivos."
    )
    return {"strengths": strengths, "improvements": improvements, "focus": focus}


def analysis_prompt(match: LocalMatchHistory, metrics: list[Metric]) -> str:
    """One compact line, so the request stays around 150 tokens."""
    result = "victoria" if match.win else "derrota"
    minutes = int(match.duration_seconds // 60)
    parts = [f"{_describe(m)} [{m.verdict}]" for m in metrics]
    return (
        f"{match.champion_name} {normalize_role(match.role)}, {result}, {minutes} min, "
        f"KDA {match.kills}/{match.deaths}/{match.assists}. " + "; ".join(parts) + "."
    )


def _clean(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("*", "").split())
    return text[:MAX_TEXT] if text else None


async def write_analysis(match: LocalMatchHistory, metrics: list[Metric]) -> tuple[dict[str, str], str]:
    """Groq first; the rules text when there is no key, an error or an incomplete reply."""
    fallback = rules_analysis(metrics)
    try:
        reply = await groq_coach.complete_json(ANALYSIS_SYSTEM_PROMPT, analysis_prompt(match, metrics), GROQ_ANALYSIS_TOKENS)
    except Exception as exc:
        logger.info("Groq analysis failed: %s", type(exc).__name__)
        reply = None
    if reply:
        texts = {
            "strengths": _clean(reply.get("bien")),
            "improvements": _clean(reply.get("mejorar")),
            "focus": _clean(reply.get("foco")),
        }
        if all(texts.values()):
            return {key: str(value) for key, value in texts.items()}, "groq"
    return fallback, "rules"


def analysis_view(row: MatchAnalysis) -> dict[str, Any]:
    try:
        metrics = json.loads(row.metrics_json or "[]")
    except json.JSONDecodeError:
        metrics = []
    return {
        "matchId": row.external_match_id,
        "championName": row.champion_name,
        "role": row.role,
        "win": row.win,
        "metrics": metrics,
        "strengths": row.strengths,
        "improvements": row.improvements,
        "focus": row.focus,
        "source": row.source,
        "createdAt": _aware(row.created_at).isoformat() if row.created_at else None,
    }


async def stored_analyses(db: AsyncSession, user_id: int, limit: int = 30) -> list[MatchAnalysis]:
    """The account's post-match reports, newest first."""
    found = await db.execute(
        select(MatchAnalysis)
        .where(MatchAnalysis.user_id == user_id)
        .order_by(MatchAnalysis.created_at.desc())
        .limit(limit)
    )
    return list(found.scalars().all())


async def stored_analysis(db: AsyncSession, user_id: int, match_id: str) -> Optional[MatchAnalysis]:
    found = await db.execute(
        select(MatchAnalysis).where(MatchAnalysis.user_id == user_id, MatchAnalysis.external_match_id == match_id)
    )
    return found.scalar_one_or_none()


async def analyze_match(db: AsyncSession, user_id: int, match: LocalMatchHistory) -> MatchAnalysis:
    """Returns the stored analysis, or writes it once. Safe to call twice at the same time."""
    existing = await stored_analysis(db, user_id, match.external_match_id)
    if existing:
        return existing

    metrics = compute_metrics(match)
    texts, source = await write_analysis(match, metrics)
    row = MatchAnalysis(
        user_id=user_id,
        external_match_id=match.external_match_id,
        champion_name=match.champion_name,
        role=normalize_role(match.role),
        win=match.win,
        metrics_json=json.dumps([asdict(m) for m in metrics]),
        strengths=texts["strengths"],
        improvements=texts["improvements"],
        focus=texts["focus"],
        source=source,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # Another request stored it first; keep that one.
        await db.rollback()
        existing = await stored_analysis(db, user_id, match.external_match_id)
        if existing:
            return existing
        raise
    return row


async def owned_match(db: AsyncSession, user_id: int, match_id: str) -> Optional[LocalMatchHistory]:
    found = await db.execute(
        select(LocalMatchHistory).where(
            LocalMatchHistory.user_id == user_id,
            LocalMatchHistory.external_match_id == match_id,
        )
    )
    return found.scalar_one_or_none()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def latest_finished_match(db: AsyncSession, user_id: int) -> Optional[LocalMatchHistory]:
    """Newest Summoner's Rift game of the last hours that is long enough to analyse (no remakes)."""
    found = await db.execute(
        select(LocalMatchHistory)
        .where(
            LocalMatchHistory.user_id == user_id,
            LocalMatchHistory.queue_id.in_(SUMMONERS_RIFT_QUEUES) | (LocalMatchHistory.game_mode == "CLASSIC"),
        )
        .order_by(LocalMatchHistory.played_at.desc())
        .limit(1)
    )
    match = found.scalar_one_or_none()
    if match is None or match.duration_seconds < MIN_DURATION_SECONDS:
        return None
    if datetime.now(timezone.utc) - _aware(match.played_at) > timedelta(hours=RECENT_HOURS):
        return None
    return match
