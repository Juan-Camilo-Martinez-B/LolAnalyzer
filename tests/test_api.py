"""
Integration and API tests for FastAPI endpoints and WebSocket /ws/events stream.
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.game_events import GameEventType, Role, TriggerType, WSMessageType


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_root_endpoint(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "LolAnalyzer" in data["app"]


def test_health_check_endpoint(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "active_ws_connections" in data


def test_champ_select_status(client: TestClient):
    response = client.get("/api/champ-select/status")
    assert response.status_code == 200
    data = response.json()
    assert "connected" in data


def test_champ_select_session_when_offline(client: TestClient):
    response = client.get("/api/champ-select/session")
    assert response.status_code == 200
    data = response.json()
    assert data["is_active"] is False


def test_websocket_ping_pong(client: TestClient):
    with client.websocket_connect("/ws/events") as websocket:
        # Send PING
        websocket.send_text(json.dumps({"type": "PING", "payload": {}}))
        response = websocket.receive_text()
        data = json.loads(response)
        assert data["type"] == WSMessageType.PONG.value
        assert data["payload"]["status"] == "alive"


def test_websocket_telemetry_tilt_trigger(client: TestClient):
    with client.websocket_connect("/ws/events") as websocket:
        # Reset session
        websocket.send_text(json.dumps({"type": "RESET", "payload": {}}))
        reset_resp = json.loads(websocket.receive_text())
        assert reset_resp["type"] == WSMessageType.RESET.value

        # Ingest 1st telemetry with death at t=60
        t1 = {
            "type": "TELEMETRY_INGEST",
            "payload": {
                "deaths": 1,
                "game_time_seconds": 60.0,
                "role": "MID",
                "champion_name": "Yasuo",
            },
        }
        # Ingest 1st death event
        websocket.send_text(
            json.dumps(
                {
                    "type": "GAME_EVENT",
                    "payload": {
                        "event_type": "DEATH",
                        "game_time_seconds": 60.0,
                    },
                }
            )
        )
        websocket.send_text(json.dumps(t1))

        # Ingest 2nd death event at t=120 (within 60 seconds)
        websocket.send_text(
            json.dumps(
                {
                    "type": "GAME_EVENT",
                    "payload": {
                        "event_type": "DEATH",
                        "game_time_seconds": 120.0,
                    },
                }
            )
        )

        t2 = {
            "type": "TELEMETRY_INGEST",
            "payload": {
                "deaths": 2,
                "game_time_seconds": 120.0,
                "role": "MID",
                "champion_name": "Yasuo",
            },
        }
        websocket.send_text(json.dumps(t2))

        # We should receive RULE_TRIGGERED for TILT_RISK
        response1 = websocket.receive_text()
        data1 = json.loads(response1)
        assert data1["type"] == WSMessageType.RULE_TRIGGERED.value
        assert data1["payload"]["trigger_type"] == TriggerType.TILT_RISK.value

        # Following RULE_TRIGGERED, we should receive TACTICAL_ADVICE
        response2 = websocket.receive_text()
        data2 = json.loads(response2)
        assert data2["type"] == WSMessageType.TACTICAL_ADVICE.value
        assert "text" in data2["payload"]
        assert len(data2["payload"]["text"].split()) <= 12
