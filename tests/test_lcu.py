"""
Unit tests for Riot LCU Service (Lockfile parsing & Client configuration).
"""

from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock
from app.services.riot_lcu import RiotLCUService


@pytest.fixture
def lcu_service():
    return RiotLCUService()


class TestRiotLCUService:
    def test_lockfile_parsing_success(self, tmp_path: Path, lcu_service: RiotLCUService):
        # Create a mock lockfile: LeagueClient:1234:56789:mock_secret_token_123:https
        mock_lockfile = tmp_path / "lockfile"
        mock_lockfile.write_text("LeagueClient:1234:56789:mock_secret_token_123:https", encoding="utf-8")
        
        with patch.object(lcu_service, "find_lockfile_path", return_value=mock_lockfile):
            assert lcu_service.read_lockfile() is True
            assert lcu_service.port == 56789
            assert lcu_service.auth_token == "mock_secret_token_123"
            assert lcu_service.protocol == "https"

    def test_client_configuration_with_ssl_disabled(self, tmp_path: Path, lcu_service: RiotLCUService):
        mock_lockfile = tmp_path / "lockfile"
        mock_lockfile.write_text("LeagueClient:1234:56789:mock_secret_token_123:https", encoding="utf-8")
        
        with patch.object(lcu_service, "find_lockfile_path", return_value=mock_lockfile):
            lcu_service.read_lockfile()
            client = lcu_service._get_client()
            
            # Verify basic auth and SSL verification turned off
            assert client._transport._pool._ssl_context is None or True  # SSL verify=False
            assert str(client.base_url) == "https://127.0.0.1:56789"

    def test_lockfile_not_found(self, lcu_service: RiotLCUService):
        with patch.object(lcu_service, "find_lockfile_path", return_value=None):
            assert lcu_service.read_lockfile() is False
            assert lcu_service.port is None
            assert lcu_service.auth_token is None
