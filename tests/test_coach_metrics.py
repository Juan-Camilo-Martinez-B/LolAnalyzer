from types import SimpleNamespace

from app.services.coach_metrics import summarize_advices


def test_summarize_advices_counts_real_rows():
    rows = [
        SimpleNamespace(
            id=2,
            advice_text="Farmea bajo torre.",
            trigger_type="CS_CRASH",
            severity="WARNING",
            champion_name="Ahri",
            role="MID",
            game_time_seconds=400,
            source="gemini",
            created_at=None,
        ),
        SimpleNamespace(
            id=1,
            advice_text="Juega defensivo.",
            trigger_type="TILT_RISK",
            severity="CRITICAL",
            champion_name="Ahri",
            role="MID",
            game_time_seconds=220,
            source="heuristic",
            created_at=None,
        ),
    ]

    summary = summarize_advices(rows)
    assert summary["total"] == 2
    assert summary["fromGemini"] == 1
    assert summary["fromHeuristic"] == 1
    assert summary["byTrigger"][0]["count"] == 1
    assert summary["recent"][0]["text"] == "Farmea bajo torre."
