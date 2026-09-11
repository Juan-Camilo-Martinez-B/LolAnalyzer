"""
LolAnalyzer Backend - Authentication REST API
Provides complete, symmetric authentication endpoints:
- Registration & Login (Local Bcrypt)
- Google OAuth2 Single Sign-On (Google ID Token)
- Token Refresh & Cryptographic Logout with JTI Blacklisting
- Current Authenticated User Inspection (/me)
"""

from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    blacklist_token,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    is_token_blacklisted,
    oauth2_scheme,
    verify_google_id_token,
    verify_password,
)
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import (
    GoogleAuthRequest,
    LogoutResponse,
    RefreshTokenRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
)

logger = logging.getLogger("lol_analyzer.auth")

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(user_in: UserRegister, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """
    Registers a new user account using email and password.
    Returns access and refresh JWT tokens upon successful creation.
    """
    # Check if email is already taken
    stmt = select(User).where(User.email == user_in.email.lower().strip())
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email address already exists.",
        )

    # Hash password securely with Bcrypt
    hashed_pwd = hash_password(user_in.password)

    new_user = User(
        email=user_in.email.lower().strip(),
        username=user_in.username.strip(),
        hashed_password=hashed_pwd,
        auth_provider="local",
        summoner_name=user_in.summoner_name.strip() if user_in.summoner_name else None,
        region=user_in.region.lower().strip(),
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    access_token, _, _ = create_access_token(new_user.id, new_user.email)
    refresh_token, _, _ = create_refresh_token(new_user.id, new_user.email)

    logger.info(f"New user registered: {new_user.email} (ID: {new_user.id})")
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """
    Authenticates an existing user with email and password.
    Returns access and refresh JWT tokens.
    """
    stmt = select(User).where(User.email == credentials.email.lower().strip())
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive or disabled.",
        )

    access_token, _, _ = create_access_token(user.id, user.email)
    refresh_token, _, _ = create_refresh_token(user.id, user.email)

    logger.info(f"User logged in: {user.email} (ID: {user.id})")
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/google", response_model=TokenResponse)
async def google_auth(request: GoogleAuthRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """
    Authenticates or auto-registers a user via Google Sign-In ID Token.
    Returns standard LolAnalyzer access and refresh JWT tokens.
    """
    google_profile = verify_google_id_token(request.id_token)
    if not google_profile or not google_profile.get("email"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unverified Google authentication token.",
        )

    email = google_profile["email"].lower().strip()
    google_id = google_profile.get("google_id")
    name = google_profile.get("name") or email.split("@")[0]
    picture = google_profile.get("picture")

    # Find user by email or google_id
    stmt = select(User).where((User.email == email) | (User.google_id == google_id))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user:
        # Update google metadata if not set
        if not user.google_id:
            user.google_id = google_id
        if picture and not user.avatar_url:
            user.avatar_url = picture
        await db.commit()
    else:
        # Auto-register new Google user
        user = User(
            email=email,
            username=name,
            auth_provider="google",
            google_id=google_id,
            avatar_url=picture,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info(f"New user registered via Google: {email} (ID: {user.id})")

    access_token, _, _ = create_access_token(user.id, user.email)
    refresh_token, _, _ = create_refresh_token(user.id, user.email)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> LogoutResponse:
    """
    Revokes the current JWT session by registering its unique JTI in the token blacklist.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required to log out.",
        )

    payload = decode_token(token)
    token_jti = payload.get("jti")
    exp = payload.get("exp")

    if not token_jti or not exp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token structure for logout.",
        )

    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
    await blacklist_token(token_jti, expires_at, db)

    logger.info(f"Token revoked (Logged out): {token_jti}")
    return LogoutResponse(
        status="logged_out",
        message="Session successfully terminated and token revoked.",
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Exchanges a valid refresh token for a newly minted access token.
    """
    payload = decode_token(request.refresh_token)
    token_jti = payload.get("jti")
    token_type = payload.get("type")
    user_id = payload.get("sub")

    if token_type != "refresh" or not token_jti or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provided token is not a valid refresh token.",
        )

    if await is_token_blacklisted(token_jti, db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked.",
        )

    stmt = select(User).where(User.id == int(user_id))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account no longer exists or is inactive.",
        )

    new_access_token, _, _ = create_access_token(user.id, user.email)
    new_refresh_token, _, _ = create_refresh_token(user.id, user.email)

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """
    Returns full profile and settings of the currently authenticated user.
    """
    return UserResponse.model_validate(current_user)
