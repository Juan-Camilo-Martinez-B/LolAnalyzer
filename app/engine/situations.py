"""
Situational coach: concrete advice from what is happening right now.

Reads the Live Client snapshot (health, mana, gold, death timer, vision and
objective events) and keeps a little state between polls, so advice is about
sustained situations ("22% health for 8 s") instead of a single frame.

Every tip is built from the numbers of the match, in Spanish, without a
language model, so it never invents a situation. Good plays get praise the same
way: multikills, first blood, aces, towers, objectives and near-perfect farming.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

# Objective timers (game seconds). Dragon and Baron are stable across patches;
# other camps change often, so they only produce "taken" messages.
DRAGON_FIRST_SPAWN = 5 * 60
DRAGON_RESPAWN = 5 * 60
ELDER_RESPAWN = 6 * 60
BARON_FIRST_SPAWN = 20 * 60
BARON_RESPAWN = 6 * 60
OBJECTIVE_WARNING_SECONDS = 60
# Older objective events still set the timers but are not announced (app opened mid-match).
STALE_EVENT_SECONDS = 30

LOW_HEALTH_RATIO = 0.30
CRITICAL_HEALTH_RATIO = 0.15
LOW_HEALTH_SECONDS = 6.0
LOW_MANA_RATIO = 0.15
LOW_MANA_SECONDS = 10.0
GOLD_BANK = 1300
GOLD_BANK_SECONDS = 40.0
SHOPPING_GOLD = 900
VISION_PER_MINUTE = 0.6
MIN_GAP_SECONDS = 15.0

# Farming praise: 10 CS/min is perfect. Checked from minute 10 so early numbers do not mislead.
CS_PRAISE_FROM_SECONDS = 10 * 60
CS_TIERS = (
    (11.0, "more_than_perfect"),
    (10.0, "perfect"),
    (9.0, "near_perfect"),
)
CS_TIER_RANK = {"near_perfect": 1, "perfect": 2, "more_than_perfect": 3}
CS_PRAISE_REPEAT_SECONDS = 300.0

MULTIKILL_TEXT = {
    2: "¡Asesinato doble! Bien ejecutado: aprovecha la ventaja para empujar o tomar un objetivo.",
    3: "¡Triple asesinato! Dominas la pelea: conviértelo en una torre o un dragón.",
    4: "¡Asesinato cuádruple! La partida está en tus manos: agrupa y ve por un objetivo grande.",
    5: "¡PENTAKILL! Jugada perfecta: es el momento de ir por el Barón o el nexo.",
}

COOLDOWNS = {
    "recall": 40.0,
    "mana": 90.0,
    "gold": 120.0,
    "dead": 0.0,
    "objective_soon": 0.0,
    "objective_taken": 0.0,
    "vision": 180.0,
    "praise": 0.0,
    "cs_praise": 0.0,
}

DRAGON_NAMES = {
    "Fire": "Infernal",
    "Water": "del Océano",
    "Earth": "de Montaña",
    "Air": "de Nube",
    "Hextech": "Hextech",
    "Chemtech": "Quimtech",
    "Elder": "Ancestral",
}


@dataclass
class LiveSituation:
    """The parts of one Live Client snapshot the situational coach needs."""

    game_time: float
    team: Optional[str]
    health_ratio: Optional[float]
    mana_ratio: Optional[float]
    gold: int
    is_dead: bool
    respawn_seconds: float
    ward_score: float
    objective_events: list[dict[str, Any]] = field(default_factory=list)
    creep_score: int = 0
    # Good plays by the local player or their team (multikills, first blood, ace, towers).
    praise_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SituationAdvice:
    key: str
    category: str
    severity: str
    text: str


def _ratio(current: Any, maximum: Any) -> Optional[float]:
    try:
        top = float(maximum)
        value = float(current)
    except (TypeError, ValueError):
        return None
    if top <= 0:
        return None
    return max(0.0, min(1.0, value / top))


def clock(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def parse_situation(payload: dict[str, Any], aliases: set[str]) -> Optional[LiveSituation]:
    """Builds a LiveSituation from allgamedata for the local player."""
    active = payload.get("activePlayer") if isinstance(payload.get("activePlayer"), dict) else {}
    stats = active.get("championStats") if isinstance(active.get("championStats"), dict) else {}
    players = payload.get("allPlayers") if isinstance(payload.get("allPlayers"), list) else []
    me = next(
        (p for p in players if isinstance(p, dict) and {
            str(p.get(k) or "") for k in ("summonerName", "riotId", "riotIdGameName")
        } & aliases),
        None,
    )
    if me is None:
        return None

    team = str(me.get("team") or "") or None
    teams = {
        name: str(p.get("team") or "")
        for p in players if isinstance(p, dict)
        for name in (p.get("summonerName"), p.get("riotId"), p.get("riotIdGameName"))
        if isinstance(name, str) and name
    }

    objectives = []
    praise = []
    events = payload.get("events") if isinstance(payload.get("events"), dict) else {}
    for raw in events.get("Events") or []:
        if not isinstance(raw, dict):
            continue
        name = raw.get("EventName")
        event_id = str(raw.get("EventID"))
        time = float(raw.get("EventTime") or 0)
        killer = str(raw.get("KillerName") or "")
        assisters = {str(item) for item in raw.get("Assisters") or []}
        mine = killer in aliases
        if name in ("DragonKill", "BaronKill", "HeraldKill", "HordeKill"):
            objectives.append({
                "id": event_id,
                "name": name,
                "time": time,
                "ally": bool(team) and teams.get(killer) == team,
                "mine": mine,
                "dragon": str(raw.get("DragonType") or ""),
                "stolen": str(raw.get("Stolen") or "").lower() == "true",
            })
        elif name == "Multikill" and mine:
            praise.append({"id": event_id, "kind": "multikill", "time": time, "value": int(raw.get("KillStreak") or 0)})
        elif name == "FirstBlood" and str(raw.get("Recipient") or "") in aliases:
            praise.append({"id": event_id, "kind": "first_blood", "time": time, "value": 0})
        elif name == "Ace" and team and str(raw.get("AcingTeam") or "") == team:
            praise.append({"id": event_id, "kind": "ace", "time": time, "value": 0})
        elif name in ("TurretKilled", "InhibKilled") and (mine or assisters & aliases):
            kind = "turret" if name == "TurretKilled" else "inhibitor"
            praise.append({"id": event_id, "kind": kind, "time": time, "value": 0})

    mana = None
    if str(stats.get("resourceType") or "").upper() == "MANA":
        mana = _ratio(stats.get("resourceValue"), stats.get("resourceMax"))

    scores = me.get("scores") if isinstance(me.get("scores"), dict) else {}
    game_data = payload.get("gameData") if isinstance(payload.get("gameData"), dict) else {}
    return LiveSituation(
        game_time=float(game_data.get("gameTime") or 0),
        team=team,
        health_ratio=_ratio(stats.get("currentHealth"), stats.get("maxHealth")),
        mana_ratio=mana,
        gold=int(float(active.get("currentGold") or 0)),
        is_dead=bool(me.get("isDead")),
        respawn_seconds=float(me.get("respawnTimer") or 0),
        ward_score=float(scores.get("wardScore") or 0),
        objective_events=objectives,
        creep_score=int(scores.get("creepScore") or 0),
        praise_events=praise,
    )


class SituationCoach:
    """Stateful rules over consecutive snapshots of one match."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.low_health_since: Optional[float] = None
        self.low_mana_since: Optional[float] = None
        self.gold_since: Optional[float] = None
        self.last_health: Optional[float] = None
        self.was_dead = False
        self.seen_objectives: set[str] = set()
        self.next_dragon: float = DRAGON_FIRST_SPAWN
        self.next_baron: float = BARON_FIRST_SPAWN
        self.dragons = {"ally": 0, "enemy": 0}
        self.announced: set[str] = set()
        self.last_fired: dict[str, float] = {}
        self.last_any: float = -9999.0
        self.seen_praise: set[str] = set()
        self.cs_tier: Optional[str] = None
        self.cs_tier_at: float = -9999.0

    # ------------------------------------------------------------------ helpers

    def _ready(self, key: str, now: float, urgent: bool = False) -> bool:
        if not urgent and now - self.last_any < MIN_GAP_SECONDS:
            return False
        return now - self.last_fired.get(key, -9999.0) >= COOLDOWNS.get(key, 60.0)

    def _fire(self, key: str, now: float, category: str, severity: str, text: str) -> SituationAdvice:
        self.last_fired[key] = now
        self.last_any = now
        return SituationAdvice(key=key, category=category, severity=severity, text=text)

    def _track(self, situation: LiveSituation) -> None:
        now = situation.game_time
        health = situation.health_ratio
        alive = not situation.is_dead

        # Health climbing fast means the player is already healing (base, potion, shield).
        healing = health is not None and self.last_health is not None and health - self.last_health > 0.15
        if alive and health is not None and health < LOW_HEALTH_RATIO and not healing:
            self.low_health_since = self.low_health_since if self.low_health_since is not None else now
        else:
            self.low_health_since = None
        self.last_health = health

        mana = situation.mana_ratio
        if alive and mana is not None and mana < LOW_MANA_RATIO:
            self.low_mana_since = self.low_mana_since if self.low_mana_since is not None else now
        else:
            self.low_mana_since = None

        if alive and situation.gold >= GOLD_BANK:
            self.gold_since = self.gold_since if self.gold_since is not None else now
        else:
            self.gold_since = None

    def _objective_taken(self, situation: LiveSituation) -> Optional[SituationAdvice]:
        advice = None
        for event in sorted(situation.objective_events, key=lambda item: item["time"]):
            if event["id"] in self.seen_objectives:
                continue
            self.seen_objectives.add(event["id"])
            side = "ally" if event["ally"] else "enemy"
            who = "Tu equipo" if event["ally"] else "El enemigo"
            stolen = " (robado)" if event["stolen"] else ""
            if event["name"] == "DragonKill":
                self.dragons[side] += 1
                elder_next = max(self.dragons.values()) >= 4
                self.next_dragon = event["time"] + (ELDER_RESPAWN if elder_next or event["dragon"] == "Elder" else DRAGON_RESPAWN)
                self.announced.discard("dragon")
                kind = DRAGON_NAMES.get(event["dragon"], "")
                label = f"el dragón {kind}".strip() if kind else "el dragón"
                follow = (
                    "Aprovecha la ventaja: presiona torres o invade."
                    if event["ally"] else "Juega del lado opuesto: placas, oleadas o el campamento enemigo."
                )
                lead = f"¡Bien jugado! Aseguraste {label}{stolen}." if event.get("mine") else f"{who} tomó {label}{stolen}."
                text = f"{lead} El próximo sale a las {clock(self.next_dragon)}. {follow}"
            elif event["name"] == "BaronKill":
                self.next_baron = event["time"] + BARON_RESPAWN
                self.announced.discard("baron")
                follow = (
                    "Agrupa y asedia con la mejora del Barón."
                    if event["ally"] else "No pelees en campo abierto: defiende bajo torre y despeja oleadas."
                )
                lead = f"¡Bien jugado! Aseguraste el Barón{stolen}." if event.get("mine") else f"{who} tomó el Barón{stolen}."
                text = f"{lead} {follow}"
            elif event["name"] == "HeraldKill":
                text = f"{who} tomó el Heraldo{stolen}." + (
                    " Úsalo en una línea con torre débil." if event["ally"] else " Cuida tu torre exterior."
                )
            else:
                text = f"{who} tomó larvas del Vacío." + (
                    "" if event["ally"] else " Atento a la presión sobre las torres."
                )
            if situation.game_time - event["time"] > STALE_EVENT_SECONDS:
                continue
            advice = SituationAdvice(
                key="objective_taken",
                category="objective",
                severity="INFO" if event["ally"] else "WARNING",
                text=text,
            )
        if advice:
            self.last_fired["objective_taken"] = situation.game_time
            self.last_any = situation.game_time
        return advice

    def _objective_soon(self, situation: LiveSituation) -> Optional[SituationAdvice]:
        now = situation.game_time
        for key, spawn, name, plan in (
            ("dragon", self.next_dragon, "El dragón", "empuja tu oleada y entra al río con visión."),
            ("baron", self.next_baron, "El Barón", "agrupa con tu equipo y limpia la visión enemiga."),
        ):
            remaining = spawn - now
            if 0 < remaining <= OBJECTIVE_WARNING_SECONDS and key not in self.announced:
                self.announced.add(key)
                return self._fire(
                    "objective_soon", now, "objective", "INFO",
                    f"{name} aparece en {clock(remaining)}: {plan}",
                )
        return None

    def _praise(self, situation: LiveSituation) -> Optional[SituationAdvice]:
        """The best good play of this snapshot: a penta beats a double, a multikill beats a tower."""
        fresh = []
        for event in situation.praise_events:
            if event["id"] in self.seen_praise:
                continue
            self.seen_praise.add(event["id"])
            if situation.game_time - event["time"] <= STALE_EVENT_SECONDS:
                fresh.append(event)
        if not fresh:
            return None
        multikills = [event for event in fresh if event["kind"] == "multikill" and event["value"] >= 2]
        if multikills:
            streak = min(5, max(event["value"] for event in multikills))
            text = MULTIKILL_TEXT[streak]
        else:
            kinds = {event["kind"] for event in fresh}
            if "first_blood" in kinds:
                text = "¡Primera sangre! Buen inicio: usa ese oro extra en tu próxima compra."
            elif "ace" in kinds:
                text = "¡Ace! Tu equipo eliminó a los cinco rivales: toma torres o un objetivo ahora."
            elif "inhibitor" in kinds:
                text = "¡Inhibidor destruido! Presiona con los súbditos súper y no te sobreextiendas."
            else:
                text = "¡Torre derribada! Rota a otra línea o ayuda a tu jungla con el siguiente objetivo."
        return self._fire("praise", situation.game_time, "praise", "INFO", text)

    def _cs_praise(self, situation: LiveSituation) -> Optional[SituationAdvice]:
        """Near-perfect (9), perfect (10) or more than perfect (11+) CS per minute, from minute 10."""
        now = situation.game_time
        if now < CS_PRAISE_FROM_SECONDS:
            return None
        per_minute = situation.creep_score / (now / 60)
        tier = next((name for limit, name in CS_TIERS if per_minute >= limit), None)
        if tier is None:
            self.cs_tier = None
            return None
        improved = CS_TIER_RANK[tier] > CS_TIER_RANK.get(self.cs_tier or "", 0)
        if not improved and now - self.cs_tier_at < CS_PRAISE_REPEAT_SECONDS:
            return None
        if not self._ready("cs_praise", now):
            return None
        self.cs_tier = tier
        self.cs_tier_at = now
        minutes = int(now // 60)
        rate = f"{per_minute:.1f} CS/min a los {minutes} min"
        if tier == "more_than_perfect":
            text = f"¡Farmeo más que perfecto! {rate}: estás por encima del estándar profesional."
        elif tier == "perfect":
            text = f"¡Farmeo perfecto! {rate}: sigue así y convierte el oro en ventaja."
        else:
            text = f"Farmeo casi perfecto: {rate}, a un paso del perfecto (10)."
        return self._fire("cs_praise", now, "praise", "INFO", text)

    # --------------------------------------------------------------- evaluate

    def evaluate(self, situation: LiveSituation) -> Optional[SituationAdvice]:
        """Returns at most one tip for this snapshot, the most urgent first."""
        now = situation.game_time
        self._track(situation)

        taken = self._objective_taken(situation)
        if taken:
            return taken

        praise = self._praise(situation)
        if praise:
            return praise

        just_died = situation.is_dead and not self.was_dead
        self.was_dead = situation.is_dead
        if just_died and situation.respawn_seconds >= 8:
            gold = situation.gold
            shop = (
                f" Aprovecha para comprar: tienes {gold} de oro."
                if gold >= SHOPPING_GOLD else " Revisa qué compras al reaparecer."
            )
            return self._fire(
                "dead", now, "death", "WARNING",
                f"Reapareces en {int(situation.respawn_seconds)} s.{shop}",
            )
        if situation.is_dead:
            return None

        if self.low_health_since is not None and situation.health_ratio is not None:
            held = now - self.low_health_since
            critical = situation.health_ratio < CRITICAL_HEALTH_RATIO
            if held >= LOW_HEALTH_SECONDS and self._ready("recall", now, urgent=critical):
                percent = round(situation.health_ratio * 100)
                extra = f" y compra: tienes {situation.gold} de oro" if situation.gold >= SHOPPING_GOLD else ""
                return self._fire(
                    "recall", now, "recall", "CRITICAL" if critical else "WARNING",
                    f"Llevas {int(held)} s con {percent}% de vida: vuelve a base{extra}.",
                )

        soon = self._objective_soon(situation)
        if soon:
            return soon

        if self.low_mana_since is not None and situation.mana_ratio is not None:
            if now - self.low_mana_since >= LOW_MANA_SECONDS and self._ready("mana", now):
                percent = round(situation.mana_ratio * 100)
                return self._fire(
                    "mana", now, "recall", "WARNING",
                    f"Te queda {percent}% de maná: vuelve a base o juega pasivo hasta recuperarlo.",
                )

        if self.gold_since is not None and now >= 180:
            if now - self.gold_since >= GOLD_BANK_SECONDS and self._ready("gold", now):
                return self._fire(
                    "gold", now, "economy", "INFO",
                    f"Tienes {situation.gold} de oro sin gastar: empuja la oleada y vuelve a comprar.",
                )

        cs_praise = self._cs_praise(situation)
        if cs_praise:
            return cs_praise

        minutes = now / 60
        if minutes >= 8 and situation.ward_score < minutes * VISION_PER_MINUTE and self._ready("vision", now):
            return self._fire(
                "vision", now, "vision", "INFO",
                f"Tu visión es baja ({situation.ward_score:.0f} a los {int(minutes)} min): "
                "usa el trinket y compra un guardián de control.",
            )
        return None
