"""Situational coach: tips come from sustained, real match situations."""

from app.engine.situations import LiveSituation, SituationCoach, parse_situation


def situation(t, **overrides):
    base = dict(
        game_time=t,
        team="ORDER",
        health_ratio=1.0,
        mana_ratio=None,
        gold=300,
        is_dead=False,
        respawn_seconds=0.0,
        ward_score=50.0,
        objective_events=[],
    )
    base.update(overrides)
    return LiveSituation(**base)


def run(coach, frames):
    return [tip for tip in (coach.evaluate(frame) for frame in frames) if tip]


def test_low_health_must_last_before_recall_advice():
    coach = SituationCoach()
    assert coach.evaluate(situation(400, health_ratio=0.2)) is None
    assert coach.evaluate(situation(403, health_ratio=0.2)) is None
    tip = coach.evaluate(situation(407, health_ratio=0.22, gold=1200))
    assert tip.key == "recall" and tip.severity == "WARNING"
    assert "22% de vida" in tip.text and "1200 de oro" in tip.text


def test_a_short_dip_in_health_says_nothing():
    coach = SituationCoach()
    tips = run(coach, [
        situation(400, health_ratio=0.2),
        situation(404, health_ratio=0.8),
        situation(408, health_ratio=0.25),
    ])
    assert tips == []


def test_healing_resets_the_low_health_timer():
    coach = SituationCoach()
    coach.evaluate(situation(400, health_ratio=0.05))
    # A jump of more than 15% means a potion, shield or the fountain.
    assert coach.evaluate(situation(404, health_ratio=0.25)) is None
    assert coach.evaluate(situation(407, health_ratio=0.26)) is None


def test_death_points_to_shopping_with_the_real_timer():
    coach = SituationCoach()
    coach.evaluate(situation(600))
    tip = coach.evaluate(situation(605, is_dead=True, respawn_seconds=24, gold=1500))
    assert tip.key == "dead"
    assert "24 s" in tip.text and "1500 de oro" in tip.text
    assert coach.evaluate(situation(610, is_dead=True, respawn_seconds=19, gold=1500)) is None


def test_dragon_warning_one_minute_before_spawn():
    coach = SituationCoach()
    assert coach.evaluate(situation(230)) is None
    tip = coach.evaluate(situation(245))
    assert tip.category == "objective" and "dragón aparece en 0:55" in tip.text
    assert coach.evaluate(situation(250)) is None


def test_enemy_dragon_sets_the_next_timer_and_suggests_a_trade():
    coach = SituationCoach()
    event = {"id": "7", "name": "DragonKill", "time": 330.0, "ally": False, "dragon": "Fire", "stolen": False}
    tip = coach.evaluate(situation(332, objective_events=[event]))
    assert tip.category == "objective" and tip.severity == "WARNING"
    assert "El enemigo tomó el dragón Infernal" in tip.text and "10:30" in tip.text
    assert coach.evaluate(situation(336, objective_events=[event])) is None


def test_old_objectives_set_timers_without_announcements():
    coach = SituationCoach()
    event = {"id": "3", "name": "DragonKill", "time": 330.0, "ally": True, "dragon": "Air", "stolen": False}
    assert coach.evaluate(situation(500, objective_events=[event])) is None
    assert coach.next_dragon == 630.0


def test_unspent_gold_and_low_vision():
    coach = SituationCoach()
    assert coach.evaluate(situation(300, gold=1500)) is None
    gold_tip = coach.evaluate(situation(341, gold=1500))
    assert gold_tip.key == "gold" and "1500 de oro" in gold_tip.text
    coach = SituationCoach()
    vision_tip = coach.evaluate(situation(720, ward_score=3))
    assert vision_tip.key == "vision" and "12 min" in vision_tip.text


def test_low_mana_only_for_mana_champions():
    coach = SituationCoach()
    tips = run(coach, [situation(400, mana_ratio=0.1), situation(411, mana_ratio=0.1)])
    assert tips and tips[0].key == "mana"
    coach = SituationCoach()
    assert run(coach, [situation(400, mana_ratio=None), situation(420, mana_ratio=None)]) == []


def test_parse_reads_health_mana_death_and_objective_side():
    payload = {
        "activePlayer": {
            "riotIdGameName": "Me",
            "currentGold": 812.4,
            "championStats": {"currentHealth": 250, "maxHealth": 1000, "resourceType": "MANA", "resourceValue": 30, "resourceMax": 300},
        },
        "allPlayers": [
            {"riotIdGameName": "Me", "team": "ORDER", "isDead": False, "respawnTimer": 0, "scores": {"wardScore": 6.5}},
            {"riotIdGameName": "Foe", "team": "CHAOS"},
        ],
        "events": {"Events": [
            {"EventID": 9, "EventName": "DragonKill", "EventTime": 400, "KillerName": "Foe", "DragonType": "Water", "Stolen": "False"},
            {"EventID": 10, "EventName": "ChampionKill", "EventTime": 410},
        ]},
        "gameData": {"gameTime": 415.5},
    }
    parsed = parse_situation(payload, {"Me"})
    assert parsed.health_ratio == 0.25 and parsed.mana_ratio == 0.1
    assert parsed.gold == 812 and parsed.ward_score == 6.5
    assert parsed.objective_events == [
        {"id": "9", "name": "DragonKill", "time": 400.0, "ally": False, "mine": False, "dragon": "Water", "stolen": False}
    ]


def test_multikills_praise_the_best_streak_of_the_snapshot():
    coach = SituationCoach()
    events = [
        {"id": "20", "kind": "multikill", "time": 700.0, "value": 2},
        {"id": "21", "kind": "multikill", "time": 702.0, "value": 3},
    ]
    tip = coach.evaluate(situation(704, praise_events=events))
    assert tip.category == "praise" and tip.text.startswith("¡Triple asesinato!")
    penta = [*events, {"id": "23", "kind": "multikill", "time": 709.0, "value": 5}]
    # Praise is not held back by the gap between tips: a penta right after a triple still shows.
    assert coach.evaluate(situation(710, praise_events=penta)).text.startswith("¡PENTAKILL!")
    assert coach.evaluate(situation(714, praise_events=penta)) is None


def test_first_blood_ace_and_towers_are_praised():
    coach = SituationCoach()
    first = coach.evaluate(situation(180, praise_events=[{"id": "1", "kind": "first_blood", "time": 178.0, "value": 0}]))
    assert first.text.startswith("¡Primera sangre!")
    tower = coach.evaluate(situation(900, praise_events=[{"id": "2", "kind": "turret", "time": 899.0, "value": 0}]))
    assert tower.text.startswith("¡Torre derribada!")
    ace = coach.evaluate(situation(1300, praise_events=[{"id": "3", "kind": "ace", "time": 1299.0, "value": 0}]))
    assert ace.text.startswith("¡Ace!")


def test_securing_the_dragon_yourself_is_congratulated():
    coach = SituationCoach()
    event = {"id": "8", "name": "DragonKill", "time": 330.0, "ally": True, "mine": True, "dragon": "Fire", "stolen": True}
    tip = coach.evaluate(situation(332, objective_events=[event]))
    assert tip.text.startswith("¡Bien jugado! Aseguraste el dragón Infernal (robado).")


def test_farming_praise_tiers_from_minute_ten():
    coach = SituationCoach()
    assert coach.evaluate(situation(540, creep_score=100)) is None  # minute 9: too early
    near = coach.evaluate(situation(600, creep_score=92))
    assert near.text.startswith("Farmeo casi perfecto: 9.2 CS/min a los 10 min")
    # Reaching a better tier is praised right away; staying in it waits five minutes.
    perfect = coach.evaluate(situation(640, creep_score=108))
    assert perfect.text.startswith("¡Farmeo perfecto! 10.1 CS/min")
    assert coach.evaluate(situation(700, creep_score=118)) is None
    beyond = coach.evaluate(situation(720, creep_score=134))
    assert beyond.text.startswith("¡Farmeo más que perfecto! 11.2 CS/min")


def test_parse_reads_multikills_first_blood_and_my_towers():
    payload = {
        "activePlayer": {"riotIdGameName": "Me", "championStats": {}},
        "allPlayers": [
            {"riotIdGameName": "Me", "team": "ORDER", "scores": {"creepScore": 150}},
            {"riotIdGameName": "Foe", "team": "CHAOS"},
        ],
        "events": {"Events": [
            {"EventID": 1, "EventName": "FirstBlood", "EventTime": 200, "Recipient": "Me"},
            {"EventID": 2, "EventName": "Multikill", "EventTime": 400, "KillerName": "Me", "KillStreak": 3},
            {"EventID": 3, "EventName": "Multikill", "EventTime": 410, "KillerName": "Foe", "KillStreak": 2},
            {"EventID": 4, "EventName": "TurretKilled", "EventTime": 500, "KillerName": "Minion_T100", "Assisters": ["Me"]},
            {"EventID": 5, "EventName": "Ace", "EventTime": 600, "AcingTeam": "ORDER"},
            {"EventID": 6, "EventName": "Ace", "EventTime": 650, "AcingTeam": "CHAOS"},
        ]},
        "gameData": {"gameTime": 660},
    }
    parsed = parse_situation(payload, {"Me"})
    assert parsed.creep_score == 150
    assert [(event["kind"], event["value"]) for event in parsed.praise_events] == [
        ("first_blood", 0), ("multikill", 3), ("turret", 0), ("ace", 0),
    ]
