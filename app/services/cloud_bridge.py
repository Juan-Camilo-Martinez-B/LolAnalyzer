"""
Local agent -> cloud API calls.

The agent bundled in the desktop app has no database credentials and no API
keys. Saving history, logging coach tips and AI requests go to the cloud with
the player's own JWT, so the cloud decides who may do what.
"""

import logging
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("lol_analyzer.cloud_bridge")

TIMEOUT_SECONDS = 20.0


class CloudUnavailable(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def cloud_configured() -> bool:
    return bool((settings.CLOUD_API_URL or "").strip())


async def cloud_post(path: str, token: Optional[str], payload: dict[str, Any]) -> Any:
    """POST to the cloud with the player's JWT. Raises CloudUnavailable on any failure."""
    base = (settings.CLOUD_API_URL or "").strip().rstrip("/")
    if not base:
        raise CloudUnavailable(503, "El agente local no tiene configurada la API en la nube.")
    if not token:
        raise CloudUnavailable(401, "Inicia sesión para guardar tus datos.")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(f"{base}{path}", json=payload, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        logger.info("Cloud request %s failed: %s", path, type(exc).__name__)
        raise CloudUnavailable(503, "No pudimos conectar con el servidor. Intenta de nuevo.") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise CloudUnavailable(response.status_code, str(detail or "El servidor rechazó la solicitud."))
    try:
        return response.json()
    except ValueError:
        return None
