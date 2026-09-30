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
from app.core.recovery import canonical_champion, canonical_elo
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
from app.db.models import SecurityAnswers, User
from app.db.session import get_db
from app.schemas.auth import (
    GoogleAuthRequest,
    LogoutResponse,
    RecoveryResetRequest,
    RecoveryStartRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
)
from app.services.auth_guard import assert_not_locked, clear_attempts, register_failure
from app.services.champion_catalog import get_champion_catalog

logger = logging.getLogger("lol_analyzer.auth")

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

GENERIC_RECOVERY_ERROR = "No se pudo verificar la identidad con esos datos."


def issue_tokens(user: User) -> TokenResponse:
    version = int(user.session_version or 1)
    access_token, _, _ = create_access_token(user.id, user.email, version)
    refresh_token, _, _ = create_refresh_token(user.id, user.email, version)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(user_in: UserRegister, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """
    Registers a new user account using email and password.
    Returns access and refresh JWT tokens upon successful creation.
    """
    email = user_in.email.lower().strip()
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        )

    catalog = await get_champion_catalog(db)
    favorite = canonical_champion(user_in.favorite_champion, catalog)
    first_main = canonical_champion(user_in.first_main, catalog)
    peak_elo = canonical_elo(user_in.peak_elo)
    if not favorite or not first_main or not peak_elo:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Recovery answers must use a known champion and rank.",
        )

    new_user = User(
        email=email,
        username=user_in.username.strip(),
        hashed_password=hash_password(user_in.password),
        auth_provider="local",
        summoner_name=user_in.summoner_name.strip() if user_in.summoner_name else None,
        region=user_in.region.lower().strip(),
        preferred_roles=user_in.preferred_roles.strip(),
        coach_sensitivity=user_in.coach_sensitivity.lower().strip(),
        session_version=1,
    )
    db.add(new_user)
    await db.flush()
    db.add(SecurityAnswers(
        user_id=new_user.id,
        favorite_champion_hash=hash_password(favorite),
        peak_elo_hash=hash_password(peak_elo),
        first_main_hash=hash_password(first_main),
    ))
    await db.commit()
    await db.refresh(new_user)

    logger.info("New user registered: id=%s", new_user.id)
    return issue_tokens(new_user)


@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """
    Authenticates an existing user with email and password.
    Returns access and refresh JWT tokens.
    """
    email = credentials.email.lower().strip()
    await assert_not_locked(db, email, "login")

    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    password_ok = bool(user and user.hashed_password and verify_password(credentials.password, user.hashed_password))
    if not password_ok:
        await register_failure(db, email, "login")
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

    await clear_attempts(db, email, "login")
    user.failed_login_count = 0
    user.locked_until = None
    await db.commit()
    logger.info("User logged in: id=%s", user.id)
    return issue_tokens(user)


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

    return issue_tokens(user)


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

    token_version = payload.get("ver")
    if token_type != "refresh" or not token_jti or not user_id or token_version is None:
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

    if int(token_version) != int(user.session_version or 1):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is no longer valid.",
        )

    return issue_tokens(user)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """
    Returns full profile and settings of the currently authenticated user.
    """
    return UserResponse.model_validate(current_user)


@router.get("/recovery-options")
async def recovery_options(db: AsyncSession = Depends(get_db)) -> dict:
    """Public catalogs for the recovery form. Answers are never included."""
    from app.core.recovery import ELO_OPTIONS
    from app.services.champion_catalog import get_champion_catalog

    champions = await get_champion_catalog(db)
    return {"elos": ELO_OPTIONS, "champions": champions}


@router.post("/forgot-password")
async def forgot_password(_: RecoveryStartRequest) -> dict:
    """
    Always returns the same body so the response does not reveal whether the email exists.
    """
    return {"status": "continue"}


@router.post("/reset-password")
async def reset_password(body: RecoveryResetRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Replaces the password when the three recovery answers match.
    Wrong answers, unknown emails and missing answers share one error.
    """
    email = body.email.strip().lower()
    await assert_not_locked(db, email, "recovery")

    catalog = await get_champion_catalog(db)
    favorite = canonical_champion(body.favorite_champion, catalog)
    first_main = canonical_champion(body.first_main, catalog)
    peak_elo = canonical_elo(body.peak_elo)

    user_result = await db.execute(select(User).where(User.email == email))
    user = user_result.scalar_one_or_none()
    answers = None
    if user is not None:
        answer_result = await db.execute(select(SecurityAnswers).where(SecurityAnswers.user_id == user.id))
        answers = answer_result.scalar_one_or_none()

    answers_match = bool(
        user and answers and favorite and first_main and peak_elo
        and verify_password(favorite, answers.favorite_champion_hash)
        and verify_password(peak_elo, answers.peak_elo_hash)
        and verify_password(first_main, answers.first_main_hash)
    )
    if not answers_match or user is None or answers is None:
        await register_failure(db, email, "recovery")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_RECOVERY_ERROR)

    user.hashed_password = hash_password(body.new_password)
    user.session_version = int(user.session_version or 1) + 1
    user.failed_login_count = 0
    user.locked_until = None
    answers.failed_attempts = 0
    answers.locked_until = None
    await clear_attempts(db, email, "recovery")
    await clear_attempts(db, email, "login")
    await db.commit()
    logger.info("Password reset completed for user id=%s", user.id)
    return {"status": "password_reset"}
