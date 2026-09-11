"""
LolAnalyzer Backend - Real-Time WebSocket API
Manages client connections with the Overwolf frontend app, processes incoming telemetry streams,
updates the sliding window buffer, and broadcasts rule triggers and tactical AI coach advice.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.engine.rules_evaluator import RulesEvaluator
from app.engine.sliding_window import SlidingWindowBuffer
from app.schemas.game_events import (
    CoachAdvice,
    GameEvent,
    GameEventType,
    PlayerTelemetry,
    Role,
    RuleSeverity,
    RuleTrigger,
    TriggerType,
    WSMessage,
    WSMessageType,
)
from app.services.gemini_service import gemini_coach_service

logger = logging.getLogger("lol_analyzer.websocket")

router = APIRouter(tags=["WebSocket"])


class ConnectionManager:
    """
    Manages active WebSocket connections from Overwolf desktop apps
    and provides real-time event broadcasting and AI dispatch.
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.buffer = SlidingWindowBuffer(window_seconds=settings.SLIDING_WINDOW_SECONDS)
        self.rules_evaluator = RulesEvaluator()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Client connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Client disconnected. Active connections: {len(self.active_connections)}")

    async def send_message(self, websocket: WebSocket, message: WSMessage) -> None:
        """Sends a structured message to a single connected client."""
        await websocket.send_text(message.model_dump_json())

    async def broadcast(self, message: WSMessage) -> None:
        """Broadcasts a structured message to all connected clients."""
        payload_text = message.model_dump_json()
        disconnected_clients = []
        for connection in self.active_connections:
            try:
                await connection.send_text(payload_text)
            except Exception as e:
                logger.warning(f"Error broadcasting to client: {e}")
                disconnected_clients.append(connection)

        for dc in disconnected_clients:
            self.disconnect(dc)

    async def handle_rule_trigger(self, trigger: RuleTrigger) -> None:
        """
        Dispatches a detected RuleTrigger through the real-time pipeline:
        1. Broadcasts the raw RULE_TRIGGERED event.
        2. Dispatches asynchronously to Gemini Coach (<12 words tactical advice).
        3. Broadcasts the resulting TACTICAL_ADVICE to all overlay HUD clients.
        """
        now = time.time()

        # Step 1: Broadcast raw rule trigger
        await self.broadcast(
            WSMessage(
                type=WSMessageType.RULE_TRIGGERED,
                payload=trigger.model_dump(),
                timestamp=now,
            )
        )

        # Step 2 & 3: Asynchronously generate tactical advice and broadcast
        try:
            advice: CoachAdvice = await gemini_coach_service.generate_tactical_advice(trigger)
            await self.broadcast(
                WSMessage(
                    type=WSMessageType.TACTICAL_ADVICE,
                    payload=advice.model_dump(),
                    timestamp=time.time(),
                )
            )
            logger.info(f"Tactical Advice emitted: '{advice.text}' (by {advice.generated_by})")
        except Exception as e:
            logger.error(f"Error generating or broadcasting tactical advice: {e}")

    def reset_session(self) -> None:
        """Resets engine sliding window and rule cooldowns for a new match."""
        self.buffer.reset()
        self.rules_evaluator.reset()
        logger.info("Engine session reset for new match.")


# Global connection manager instance
manager = ConnectionManager()


@router.websocket("/ws/events")
async def websocket_events_endpoint(websocket: WebSocket):
    """
    Bidirectional WebSocket endpoint for live in-game telemetry exchange:
    - Receives: Telemetry ingests, discrete events (kills, deaths, summoners), pings, resets.
    - Emits: Heuristic triggers, tactical advice payloads, pongs, status updates.
    """
    await manager.connect(websocket)

    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                await manager.send_message(
                    websocket,
                    WSMessage(
                        type=WSMessageType.ERROR,
                        payload={"detail": "Invalid JSON format received."},
                        timestamp=time.time(),
                    ),
                )
                continue

            msg_type = data.get("type")
            payload = data.get("payload", {})

            # Handle PING
            if msg_type == WSMessageType.PING.value or msg_type == "PING":
                await manager.send_message(
                    websocket,
                    WSMessage(type=WSMessageType.PONG, payload={"status": "alive"}, timestamp=time.time()),
                )
                continue

            # Handle RESET (e.g. game restart or new match)
            if msg_type == WSMessageType.RESET.value or msg_type == "RESET":
                manager.reset_session()
                await manager.send_message(
                    websocket,
                    WSMessage(type=WSMessageType.RESET, payload={"status": "reset_complete"}, timestamp=time.time()),
                )
                continue

            # Handle TELEMETRY_INGEST
            if msg_type == WSMessageType.TELEMETRY_INGEST.value or msg_type == "TELEMETRY_INGEST":
                try:
                    telemetry = PlayerTelemetry(**payload)
                    manager.buffer.update_telemetry(telemetry)

                    # Evaluate heuristic rules on updated state
                    trigger = manager.rules_evaluator.evaluate(manager.buffer, telemetry)
                    if trigger:
                        await manager.handle_rule_trigger(trigger)
                except Exception as e:
                    logger.error(f"Failed to process telemetry ingest: {e}")

                continue

            # Handle discrete GAME_EVENT (Kill, Death, Summoner Used, etc.)
            if msg_type == WSMessageType.GAME_EVENT.value or msg_type == "GAME_EVENT":
                try:
                    event = GameEvent(**payload)
                    trigger = manager.rules_evaluator.evaluate_event(manager.buffer, event)
                    if trigger:
                        await manager.handle_rule_trigger(trigger)
                except Exception as e:
                    logger.error(f"Failed to process game event: {e}")

                continue

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket loop: {e}")
        manager.disconnect(websocket)
