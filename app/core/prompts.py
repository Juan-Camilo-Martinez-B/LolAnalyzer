"""
LolAnalyzer Backend - Tactical Coach System Prompts
Role-tailored system prompts for Google Gemini 1.5 Flash.
Enforces strict conciseness (< 12 words), imperative tone, and zero conversational filler.
"""

from typing import Dict
from app.schemas.game_events import PlayerTelemetry, Role, RuleTrigger, TriggerType

# Base Coach Persona and Strict Formatting Rule
BASE_COACH_INSTRUCTION = """
Eres LolAnalyzer AI Co-Pilot, un coach táctico de élite para League of Legends en tiempo real.
Tu objetivo es dar una instrucción táctica inmediata, constructiva e imperativa para corregir errores o prevenir muertes.

REGLAS ESTRICTAS DE RESPUESTA:
1. Longitud MÁXIMA: 12 palabras.
2. Modo IMPERATIVO directo (ej. "Farmea bajo torre...", "Evita pelear...", "Agrupa en...").
3. CERO saludos, CERO explicaciones, CERO introducciones, CERO relleno.
4. Solo el consejo táctico en español directo.
"""

ROLE_SPECIFIC_FOCUS: Dict[Role, str] = {
    Role.TOP: """
Enfoque TOP LANE:
- Control de oleadas (congelar bajo torre, slow push).
- Evitar sobre-extensión en 1v1 sin visión del jungla.
- Gestión de Teleport y juego en split push seguro.
""",
    Role.JUNGLE: """
Enfoque JUNGLA:
- Rutas de farmeo seguras para recuperar tempo y nivel.
- Control de objetivos neutrales (Dragón, Barón, Larvas del Vacío).
- Evitar invadir sin prioridad en líneas adyacentes.
""",
    Role.MID: """
Enfoque MID LANE:
- Gestión de oleadas y prioridad para rotaciones seguras.
- Evitar morir en el río o trades forzados sin visión en arbustos.
- Mantener distancia segura contra asesinos y ganks.
""",
    Role.ADC: """
Enfoque ADC / BOT CARRY:
- Posicionamiento seguro detrás de la línea frontal / torre.
- Farmeo seguro de oleadas sin arriesgar sin tu soporte.
- Preservar Destello / Purificar para peleas de equipo decisivas.
""",
    Role.SUPPORT: """
Enfoque SOPORTE:
- Establecer visión segura en entradas de río y objetivos.
- Priorizar protección / peel a tu tirador o carry federado.
- Evitar quedar aislado en el mapa al colocar guardianes.
""",
    Role.UNKNOWN: """
Enfoque GENERAL:
- Juego defensivo, estabilizar economía y priorizar supervivencia.
""",
}


def get_system_prompt_for_role(role: Role) -> str:
    """Combines base tactical instruction with role-specific strategic guidance."""
    role_focus = ROLE_SPECIFIC_FOCUS.get(role, ROLE_SPECIFIC_FOCUS[Role.UNKNOWN])
    return f"{BASE_COACH_INSTRUCTION.strip()}\n\n{role_focus.strip()}"


def build_trigger_prompt(trigger: RuleTrigger) -> str:
    """
    Constructs a compressed contextual prompt for Gemini 1.5 Flash
    from the detected RuleTrigger and player telemetry.
    """
    t = trigger.telemetry_snapshot
    minutes = int(t.game_time_seconds // 60)
    seconds = int(t.game_time_seconds % 60)
    time_str = f"{minutes:02d}:{seconds:02d}"

    lines = [
        f"ESTADO DE LA PARTIDA [{time_str}]:",
        f"- Campeón: {t.champion_name} ({t.role.value}) | Nivel: {t.level}",
        f"- KDA: {t.kills}/{t.deaths}/{t.assists} | CS: {t.cs} | Oro actual: {t.current_gold}",
        f"- Alerta del Motor: {trigger.trigger_type.value} ({trigger.severity.value})",
        f"- Motivo: {trigger.reason}",
    ]

    if trigger.context_data:
        ctx_details = ", ".join(f"{k}: {v}" for k, v in trigger.context_data.items())
        lines.append(f"- Contexto extra: {ctx_details}")

    lines.append("\nGenera tu instrucción imperativa de MÁXIMO 12 palabras:")
    return "\n".join(lines)


# Short and stable. Role essays and match JSON stay out of the Groq request.
COMPACT_SYSTEM_PROMPT = (
    "Coach de League of Legends. Una orden imperativa en español, máximo 12 palabras. "
    "Sin saludo ni explicación."
)


def _short_metric(value: object, limit: int = 16) -> str | None:
    """Keeps a single token. Dicts, lists, and long strings are dropped."""
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (str, int, float)):
        return None
    text = str(value).strip()
    if not text or len(text) > limit:
        return None
    return text


def _cs_per_minute(telemetry: PlayerTelemetry) -> float:
    minutes = telemetry.game_time_seconds / 60 if telemetry.game_time_seconds > 0 else 0
    if minutes <= 0:
        return 0.0
    return round(telemetry.cs / minutes, 1)


def coach_state_key(trigger: RuleTrigger) -> str:
    """
    Identity of the advice request. Gold, raw events, and match JSON do not
    change the key, so an unchanged board does not call the model again.
    """
    telemetry = trigger.telemetry_snapshot
    phase = _short_metric(trigger.context_data.get("phase")) or "live"
    recent = _short_metric(trigger.context_data.get("recent_deaths")) or "na"
    return "|".join(
        [
            telemetry.champion_name,
            phase,
            f"{telemetry.kills}/{telemetry.deaths}/{telemetry.assists}",
            f"{_cs_per_minute(telemetry)}",
            recent,
            trigger.trigger_type.value,
        ]
    )


def build_compact_coach_prompt(trigger: RuleTrigger) -> str:
    """
    Metrics only: champion, phase, KDA, CS/min, recent deaths, and the alert.
    Never serializes context_data or a match payload.
    """
    telemetry = trigger.telemetry_snapshot
    phase = _short_metric(trigger.context_data.get("phase")) or "live"
    recent = _short_metric(trigger.context_data.get("recent_deaths")) or "na"
    return (
        f"champ={telemetry.champion_name} role={telemetry.role.value} phase={phase} "
        f"kda={telemetry.kills}/{telemetry.deaths}/{telemetry.assists} "
        f"cs/min={_cs_per_minute(telemetry)} recent_deaths={recent} "
        f"alert={trigger.trigger_type.value}"
    )
