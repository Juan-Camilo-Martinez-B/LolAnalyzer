"""
API module for LolAnalyzer Backend.
"""

from app.api.auth import router as auth_router
from app.api.champ_select import router as champ_select_router
from app.api.profile import router as profile_router
from app.api.websocket import manager as ws_manager
from app.api.websocket import router as websocket_router

__all__ = ["websocket_router", "champ_select_router", "auth_router", "profile_router", "ws_manager"]

