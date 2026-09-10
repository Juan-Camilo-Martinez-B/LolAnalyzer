"""
LolAnalyzer Backend - Riot League Client Update (LCU) Local Service
Connects to the local League Client using psutil process inspection,
parses the dynamic lockfile, and provides asynchronous HTTP API access
with HTTP Basic auth and SSL verification disabled.
"""

import base64
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
import psutil

from app.core.config import settings
from app.schemas.game_events import ChampSelectSession, Role

logger = logging.getLogger("lol_analyzer.riot_lcu")


class RiotLCUService:
    """
    Asynchronous client for interacting with the local League of Legends Client Update (LCU) API.
    """

    TARGET_PROCESS_NAMES = [
        "LeagueClientUx.exe",
        "LeagueClient.exe",
        "LeagueClientUx",
        "LeagueClient",
    ]

    COMMON_LOCKFILE_PATHS = [
        Path(r"C:\Riot Games\League of Legends\lockfile"),
        Path(r"D:\Riot Games\League of Legends\lockfile"),
        Path(r"E:\Riot Games\League of Legends\lockfile"),
    ]

    def __init__(self):
        self.port: Optional[int] = None
        self.auth_token: Optional[str] = None
        self.protocol: str = "https"
        self._client: Optional[httpx.AsyncClient] = None
        self.lockfile_path: Optional[Path] = None

    def find_lockfile_path(self) -> Optional[Path]:
        """
        Detects the active League of Legends client process using psutil
        and locates its lockfile directory.
        """
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                proc_name = proc.info.get("name") or ""
                if proc_name in self.TARGET_PROCESS_NAMES:
                    exe_path = proc.info.get("exe")
                    if exe_path:
                        candidate_path = Path(exe_path).parent / "lockfile"
                        if candidate_path.exists():
                            logger.info(f"Lockfile located via psutil process inspection at: {candidate_path}")
                            return candidate_path
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # Fallback to standard drive locations
        for fallback in self.COMMON_LOCKFILE_PATHS:
            if fallback.exists():
                logger.info(f"Lockfile located via standard fallback path at: {fallback}")
                return fallback

        return None

    def read_lockfile(self) -> bool:
        """
        Parses the League Client lockfile format:
        <process_name>:<pid>:<port>:<auth_token>:<protocol>
        """
        lockfile = self.find_lockfile_path()
        if not lockfile or not lockfile.exists():
            self.lockfile_path = None
            return False

        try:
            content = lockfile.read_text(encoding="utf-8").strip()
            parts = content.split(":")
            if len(parts) >= 5:
                # Format: name:pid:port:password:protocol
                self.port = int(parts[2])
                self.auth_token = parts[3]
                self.protocol = parts[4]
                self.lockfile_path = lockfile
                logger.info(f"LCU credentials parsed successfully. Port: {self.port}, Protocol: {self.protocol}")
                return True
        except Exception as e:
            logger.warning(f"Failed to read or parse lockfile: {e}")

        return False

    def _get_client(self) -> httpx.AsyncClient:
        """
        Creates or returns an active httpx.AsyncClient instance
        configured with Basic Auth ('riot', auth_token) and verify=False.
        """
        if self._client is None or self._client.is_closed:
            auth = httpx.BasicAuth("riot", self.auth_token or "")
            self._client = httpx.AsyncClient(
                base_url=f"{self.protocol}://127.0.0.1:{self.port}",
                auth=auth,
                verify=False,  # Riot uses local self-signed SSL certificates
                timeout=settings.LCU_TIMEOUT,
            )
        return self._client

    async def ensure_connection(self) -> bool:
        """Verifies active connection parameters or attempts re-discovery."""
        if self.port and self.auth_token and self.lockfile_path and self.lockfile_path.exists():
            return True
        return self.read_lockfile()

    async def get_current_summoner(self) -> Optional[Dict[str, Any]]:
        """
        Queries GET /lol-summoner/v1/current-summoner.
        Returns active logged-in player profile data.
        """
        if not await self.ensure_connection():
            return None

        try:
            client = self._get_client()
            resp = await client.get("/lol-summoner/v1/current-summoner")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"LCU /current-summoner query failed: {e}")

        return None

    async def get_champ_select_session(self) -> Optional[Dict[str, Any]]:
        """
        Queries GET /lol-champ-select/v1/session.
        Returns live pick/ban state during Champion Select.
        """
        if not await self.ensure_connection():
            return None

        try:
            client = self._get_client()
            resp = await client.get("/lol-champ-select/v1/session")
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                # 404 is normal when not in Champ Select
                return None
        except Exception as e:
            logger.debug(f"LCU /champ-select/v1/session query failed: {e}")

        return None

    async def get_gameflow_phase(self) -> Optional[str]:
        """
        Queries GET /lol-gameflow/v1/gameflow-phase.
        Examples: 'Lobby', 'Matchmaking', 'ChampSelect', 'InProgress', 'WaitingForStats', 'None'
        """
        if not await self.ensure_connection():
            return None

        try:
            client = self._get_client()
            resp = await client.get("/lol-gameflow/v1/gameflow-phase")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"LCU /gameflow-phase query failed: {e}")

        return None

    async def close(self) -> None:
        """Closes any open async HTTP client sessions."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Singleton instance for easy import across the application
riot_lcu_service = RiotLCUService()
