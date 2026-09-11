"""
End-to-end integration test suite simulating a full LoL match telemetry lifecycle
across the FastAPI WebSocket engine, Rules Evaluator, and Gemini Tactical Coach.
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.game_events import TriggerType, WSMessageType


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestMatchSimulationE2E:
    def test_full_match_telemetry_and_coaching_lifecycle(self, client: TestClient):
        with client.websocket_connect("/ws/events") as ws:
            # 1. Start / Reset Session
            ws.send_text(json.dumps({"type": "RESET", "payload": {}}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == WSMessageType.RESET.value

            # 2. Game Start (t=0s, Level 1 Mid Laner)
            ws.send_text(
                json.dumps(
                    {
                        "type": "TELEMETRY_INGEST",
                        "payload": {
                            "summoner_name": "TestPlayer",
                            "champion_name": "Syndra",
                            "role": "MID",
                            "level": 1,
                            "kills": 0,
                            "deaths": 0,
                            "assists": 0,
                            "cs": 0,
                            "game_time_seconds": 0.0,
                        },
                    }
                )
            )

            # 3. Laning phase farming (t=180s, CS=18)
            ws.send_text(
                json.dumps(
                    {
                        "type": "TELEMETRY_INGEST",
                        "payload": {
                            "summoner_name": "TestPlayer",
                            "champion_name": "Syndra",
                            "role": "MID",
                            "level": 3,
                            "kills": 0,
                            "deaths": 0,
                            "assists": 0,
                            "cs": 18,
                            "game_time_seconds": 180.0,
                        },
                    }
                )
            )

            # 4. First death at t=240s
            ws.send_text(
                json.dumps(
                    {
                        "type": "GAME_EVENT",
                        "payload": {
                            "event_type": "DEATH",
                            "game_time_seconds": 240.0,
                        },
                    }
                )
            )
            ws.send_text(
                json.dumps(
                    {
                        "type": "TELEMETRY_INGEST",
                        "payload": {
                            "summoner_name": "TestPlayer",
                            "champion_name": "Syndra",
                            "role": "MID",
                            "level": 4,
                            "kills": 0,
                            "deaths": 1,
                            "assists": 0,
                            "cs": 22,
                            "game_time_seconds": 240.0,
                        },
                    }
                )
            )

            # 5. Second rapid death at t=310s (70s later -> Tilt Risk Trigger)
            ws.send_text(
                json.dumps(
                    {
                        "type": "GAME_EVENT",
                        "payload": {
                            "event_type": "DEATH",
                            "game_time_seconds": 310.0,
                        },
                    }
                )
            )
            ws.send_text(
                json.dumps(
                    {
                        "type": "TELEMETRY_INGEST",
                        "payload": {
                            "summoner_name": "TestPlayer",
                            "champion_name": "Syndra",
                            "role": "MID",
                            "level": 4,
                            "kills": 0,
                            "deaths": 2,
                            "assists": 0,
                            "cs": 24,
                            "game_time_seconds": 310.0,
                        },
                    }
                )
            )

            # Assert receiving RULE_TRIGGERED for Tilt Risk
            rule_msg = json.loads(ws.receive_text())
            assert rule_msg["type"] == WSMessageType.RULE_TRIGGERED.value
            assert rule_msg["payload"]["trigger_type"] == TriggerType.TILT_RISK.value

            # Assert receiving TACTICAL_ADVICE (<12 words)
            advice_msg = json.loads(ws.receive_text())
            assert advice_msg["type"] == WSMessageType.TACTICAL_ADVICE.value
            advice_text = advice_msg["payload"]["text"]
            assert len(advice_text.split()) <= 12
            assert len(advice_text) > 0
