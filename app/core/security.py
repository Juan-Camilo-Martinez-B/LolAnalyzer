"""
LolAnalyzer Backend - Security, Cryptography and Authentication Module
Provides direct Bcrypt password hashing, JWT token management with revocation blacklist,
Google OAuth2 ID token verification, and FastAPI dependency injection for current user.
"""

from datetime import datetime, timedelta, timezone
import logging
import uuid
from typing import Any, Dict, Optional, Tuple

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import TokenBlacklist, User
from app.db.session import get_db

logger = logging.getLogger("lol_analyzer.security")

# OAuth2 Scheme for Bearer Token Extraction
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    """Hashes a plaintext password using native Bcrypt."""
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against its Bcrypt hash."""
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_token(
    data: Dict[str, Any],
    expires_delta: timedelta,
    token_type: str = "access",
) -> Tuple[str, str, datetime]:
    """
    Encodes a signed JWT containing a unique token ID (jti) for blacklist tracking.
    Returns: (encoded_token_string, token_jti, expiration_datetime)
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + expires_delta
    token_jti = str(uuid.uuid4())

    to_encode.update({
        "jti": token_jti,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    })

    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt, token_jti, expire


def create_access_token(user_id: int, email: str) -> Tuple[str, str, datetime]:
    """Generates a standard access token valid for settings.ACCESS_TOKEN_EXPIRE_MINUTES."""
    expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return create_token(
        data={"sub": str(user_id), "email": email},
        expires_delta=expires_delta,
        token_type="access",
    )


def create_refresh_token(user_id: int, email: str) -> Tuple[str, str, datetime]:
    """Generates a refresh token valid for settings.REFRESH_TOKEN_EXPIRE_DAYS."""
    expires_delta = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return create_token(
        data={"sub": str(user_id), "email": email},
        expires_delta=expires_delta,
        token_type="refresh",
    )


def decode_token(token: str) -> Dict[str, Any]:
    """
    Decodes and validates a JWT token.
    Raises HTTPException on signature expiration or tampering.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token signature has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_google_id_token(token_str: str) -> Optional[Dict[str, Any]]:
    """
    Cryptographically verifies a Google ID token with Google's public certificates.
    Returns decoded user profile: {email, name, picture, google_id} or None.
    """
    try:
        client_id = settings.GOOGLE_CLIENT_ID.strip() if settings.GOOGLE_CLIENT_ID else None
        id_info = google_id_token.verify_oauth2_token(
            token_str,
            google_requests.Request(),
            audience=client_id,
        )

        return {
            "email": id_info.get("email"),
            "name": id_info.get("name", id_info.get("email", "GoogleUser")),
            "picture": id_info.get("picture"),
            "google_id": id_info.get("sub"),
        }
    except Exception as e:
        logger.warning(f"Google ID token verification failed: {e}")
        return None


async def is_token_blacklisted(token_jti: str, db: AsyncSession) -> bool:
    """Checks whether a token's JTI has been revoked via Logout."""
    stmt = select(TokenBlacklist).where(TokenBlacklist.token_jti == token_jti)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def blacklist_token(token_jti: str, expires_at: datetime, db: AsyncSession) -> None:
    """Registers a token in the blacklist to revoke its validity."""
    entry = TokenBlacklist(token_jti=token_jti, expires_at=expires_at)
    db.add(entry)
    await db.commit()


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI security dependency protecting authenticated endpoints.
    Verifies token validity, checks revocation blacklist, and retrieves active user.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    token_jti = payload.get("jti")
    token_type = payload.get("type")
    user_id = payload.get("sub")

    if token_type != "access" or not token_jti or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload structure.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check revocation blacklist
    if await is_token_blacklisted(token_jti, db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked (User logged out).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    stmt = select(User).where(User.id == uid)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User associated with token no longer exists.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive or disabled.",
        )

    return user
