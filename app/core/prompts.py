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
