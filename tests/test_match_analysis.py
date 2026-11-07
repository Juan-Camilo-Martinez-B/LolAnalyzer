"""Post-match analysis and lane plan. Groq is faked or off; nothing reaches the network."""

import asyncio
from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient

from app.main import app  # noqa: I001 - the app must load before the models (import cycle)
from app.db.models import LocalMatchHistory
from app.services import match_analysis, matchup_plan
from app.services.match_analysis import compute_metrics, rules_analysis
from app.services.matchup_plan import rules_plan


def game(**overrides) -> LocalMatchHistory:
    values = dict(
        user_id=1,
        external_match_id="900",
        champion_name="Yone",
        role="MIDDLE",
        kills=8,
        deaths=7,
        assists=4,
        cs=180,
        duration_seconds=30 * 60,
        win=False,
        vision_score=12,
        damage_dealt=27000,
        gold_earned=12600,
        queue_id=420,
        game_mode="Ranked Solo",
        played_at=datetime.now(timezone.utc),
    )
    values.update(overrides)
    return LocalMatchHistory(**values)


def test_metrics_compare_each_number_with_the_role_reference():
    metrics = {m.key: m for m in compute_metrics(game(deaths=12))}
    assert metrics["cs"].value == 6.0 and metrics["cs"].target == 8.0 and metrics["cs"].verdict == "bad"
    assert metrics["deaths"].unit == "muertes cada 10 min" and metrics["deaths"].verdict == "bad"
    assert metrics["damage"].verdict == "good"
    assert "cs" not in {m.key for m in compute_metrics(game(role="UTILITY"))}


def test_rules_analysis_names_the_real_numbers():
    texts = rules_analysis(compute_metrics(game()))
    assert "Daño a campeones" in texts["strengths"]
    # Vision (0.4 of 0.8) is the weakest number, so it leads and sets the focus.
    assert texts["improvements"].startswith("A mejorar: Visión: 0.4 visión/min (meta 0.8)")
    assert "guardián de control" in texts["focus"]


def test_groq_reply_is_used_when_complete(monkeypatch):
    async def fake(system, prompt, max_tokens):
        assert "Yone MID" in prompt and max_tokens <= 220
        return {"bien": "Buen daño: 900/min.", "mejorar": "Farmeo de 6.0 CS/min.", "foco": "Último golpe."}

    monkeypatch.setattr(match_analysis.groq_coach, "complete_json", fake)
    texts, source = asyncio.run(match_analysis.write_analysis(game(), compute_metrics(game())))
    assert source == "groq" and texts["strengths"] == "Buen daño: 900/min."


def test_incomplete_groq_reply_falls_back_to_rules(monkeypatch):
    async def partial(*_args):
        return {"bien": "Solo esto"}

    monkeypatch.setattr(match_analysis.groq_coach, "complete_json", partial)
    _texts, source = asyncio.run(match_analysis.write_analysis(game(), compute_metrics(game())))
    assert source == "rules"


def test_lane_plan_reads_the_matchup_facts():
    plan = rules_plan("Yone", "Ahri", {"laneAdvantage": "Ahri", "soloKillAdvantage": "Yone", "playStyle": "even"})
    assert "Ahri tiene ventaja de línea" in plan["lane"]
    assert "asesinato en solitario" in plan["fight"]


def test_groq_lane_plan_is_cached_per_matchup(monkeypatch):
    calls = []

    async def fake(system, prompt, max_tokens):
        calls.append(prompt)
        return {"linea": "Juega seguro.", "pelear": "Tras su E.", "evitar": "Su encanto."}

    matchup_plan._cache.clear()
    monkeypatch.setattr(matchup_plan.groq_coach, "complete_json", fake)
    first = asyncio.run(matchup_plan.matchup_plan("Yone", "Ahri", "mid", {"tip": "Dodge her E."}))
    second = asyncio.run(matchup_plan.matchup_plan("Yone", "Ahri", "mid", {"tip": "Dodge her E."}))
    assert first == second and first["source"] == "groq" and len(calls) == 1
    assert "Dodge her E." in calls[0]
    matchup_plan._cache.clear()


def _headers(client: TestClient) -> tuple[dict[str, str], int]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"analysis_{uuid.uuid4().hex[:8]}@example.com",
            "username": "AnalysisUser",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "favorite_champion": "Ahri",
            "peak_elo": "oro",
            "first_main": "Jinx",
        },
    )
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    user_id = client.get("/api/auth/me", headers=headers).json()["id"]
    return headers, user_id


def _store(user_id: int, **overrides) -> str:
    from app.db.session import AsyncSessionLocal

    row = game(user_id=user_id, external_match_id=f"g{uuid.uuid4().hex[:10]}", **overrides)

    async def save():
        async with AsyncSessionLocal() as db:
            db.add(row)
            await db.commit()

    asyncio.run(save())
    return row.external_match_id


def test_analysis_routes_store_one_analysis_per_game(monkeypatch):
    async def no_sync(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.api.analysis.sync_lcu_history", no_sync)
    with TestClient(app) as client:
        headers, user_id = _headers(client)
        assert client.post("/api/analysis/latest").status_code == 401
        assert client.post("/api/analysis/latest", headers=headers).json() == {"analysis": None}

        match_id = _store(user_id)
        latest = client.post("/api/analysis/latest", headers=headers).json()["analysis"]
        assert latest["matchId"] == match_id and latest["source"] == "rules"
        again = client.post(f"/api/analysis/{match_id}", headers=headers).json()
        assert again["createdAt"] == latest["createdAt"]
        assert client.get(f"/api/analysis/{match_id}", headers=headers).json()["focus"] == latest["focus"]

        assert client.get("/api/analysis/unknown", headers=headers).status_code == 404
        assert client.post("/api/analysis/../etc", headers=headers).status_code in (404, 405, 422)


def test_remakes_and_old_games_are_not_auto_analysed(monkeypatch):
    async def no_sync(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.api.analysis.sync_lcu_history", no_sync)
    with TestClient(app) as client:
        headers, user_id = _headers(client)
        _store(user_id, duration_seconds=180)
        assert client.post("/api/analysis/latest", headers=headers).json() == {"analysis": None}
        _store(user_id, played_at=datetime.now(timezone.utc) - timedelta(days=1), duration_seconds=1800)
        assert client.post("/api/analysis/latest", headers=headers).json() == {"analysis": None}
