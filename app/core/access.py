"""
Who may call routes that only touch the player's own League client.

In full and cloud modes it is the signed-in account (JWT). In local mode the
agent has no database and no signing key, so it checks the random token the
desktop app created at launch; a web page open in the browser cannot know it.
The player's JWT still travels to the agent, which forwards it to the cloud
for anything that writes data or uses AI.
"""

import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import get_current_user, oauth2_scheme
from app.db.models import User
from app.db.session import get_db


def bearer_token(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    """The player's JWT as sent by the desktop, to forward to the cloud."""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


def check_agent_token(value: Optional[str]) -> None:
    expected = settings.AGENT_TOKEN or ""
    if not expected or not value or not hmac.compare_digest(value, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agente local no autorizado.")


async def require_player(
    token: Optional[str] = Depends(oauth2_scheme),
    x_agent_token: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """
    JWT account in full/cloud mode; the agent token in local mode (returns None).
    Decided per request, so one process never mixes both rules.
    """
    if settings.mode == "local":
        check_agent_token(x_agent_token)
        return None
    return await get_current_user(token, db)
