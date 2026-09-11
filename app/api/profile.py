"""
LolAnalyzer Backend - Summoner Profile REST API
Provides comprehensive profile management, LCU sync, password change,
and cryptographically verified secure account deletion.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    blacklist_token,
    decode_token,
    get_current_user,
    hash_password,
    oauth2_scheme,
    verify_password,
)
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import UserResponse
from app.schemas.profile import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    ProfileResponse,
    ProfileUpdate,
)
from app.services.riot_lcu import riot_lcu_service

logger = logging.getLogger("lol_analyzer.profile")

router = APIRouter(prefix="/api/profile", tags=["Profile"])


@router.get("", response_model=ProfileResponse)
async def get_profile(
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    """
    Returns full profile details of the authenticated user, including
    LCU live connection status and total matches recorded.
    """
    is_lcu_live = await riot_lcu_service.ensure_connection()
    total_matches = len(current_user.matches) if current_user.matches else 0

    return ProfileResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        auth_provider=current_user.auth_provider,
        avatar_url=current_user.avatar_url,
        summoner_name=current_user.summoner_name,
        summoner_icon_id=current_user.summoner_icon_id,
        region=current_user.region,
        preferred_roles=current_user.preferred_roles,
        coach_sensitivity=current_user.coach_sensitivity,
        created_at=current_user.created_at,
        lcu_live_connected=is_lcu_live,
        total_matches_recorded=total_matches,
    )


@router.put("", response_model=UserResponse)
async def update_profile(
    update_data: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Updates editable profile fields (username, avatar, preferred roles, coach sensitivity, region).
    """
    if update_data.username is not None:
        current_user.username = update_data.username.strip()
    if update_data.avatar_url is not None:
        current_user.avatar_url = update_data.avatar_url.strip()
    if update_data.preferred_roles is not None:
        current_user.preferred_roles = update_data.preferred_roles.strip()
    if update_data.coach_sensitivity is not None:
        current_user.coach_sensitivity = update_data.coach_sensitivity.lower().strip()
    if update_data.region is not None:
        current_user.region = update_data.region.lower().strip()
    if update_data.summoner_name is not None:
        current_user.summoner_name = update_data.summoner_name.strip()

    await db.commit()
    await db.refresh(current_user)

    logger.info(f"Profile updated for user: {current_user.email}")
    return UserResponse.model_validate(current_user)


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Changes user password after verifying current password.
    """
    if current_user.auth_provider == "local" or current_user.hashed_password:
        if not current_user.hashed_password or not verify_password(request.current_password, current_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Incorrect current password.",
            )

    current_user.hashed_password = hash_password(request.new_password)
    await db.commit()

    logger.info(f"Password changed for user: {current_user.email}")
    return {
        "status": "password_changed",
        "message": "Password updated successfully.",
    }


@router.post("/link-lcu", response_model=UserResponse)
async def link_lcu_summoner(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Queries local Riot LCU client and links active summoner name and profile icon to the account.
    """
    summoner_data = await riot_lcu_service.get_current_summoner()
    if not summoner_data:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="League of Legends client is not currently running or not connected via LCU.",
        )

    display_name = summoner_data.get("displayName") or summoner_data.get("gameName")
    profile_icon_id = summoner_data.get("profileIconId")

    if not display_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to extract summoner name from active client.",
        )

    current_user.summoner_name = display_name
    current_user.summoner_icon_id = profile_icon_id
    await db.commit()
    await db.refresh(current_user)

    logger.info(f"Linked summoner '{display_name}' to user {current_user.email}")
    return UserResponse.model_validate(current_user)


@router.post("/unlink-lcu", response_model=UserResponse)
async def unlink_lcu_summoner(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Unlinks connected League of Legends summoner profile from user account.
    """
    current_user.summoner_name = None
    current_user.summoner_icon_id = None
    await db.commit()
    await db.refresh(current_user)

    logger.info(f"Unlinked summoner from user {current_user.email}")
    return UserResponse.model_validate(current_user)


@router.delete("/delete-account")
async def delete_account(
    request: DeleteAccountRequest,
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Permanently deletes user account and all cascaded match history records.
    Strictly verifies user's current password and revokes the active session token.
    """
    if request.confirmation_text.strip().upper() != "DELETE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation text must be 'DELETE' to permanently delete account.",
        )

    # For local users, password verification is strictly required
    if current_user.auth_provider == "local":
        if not request.password or not current_user.hashed_password or not verify_password(request.password, current_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect password. Account deletion unauthorized.",
            )

    # Revoke current token
    payload = decode_token(token)
    token_jti = payload.get("jti")
    exp = payload.get("exp")
    if token_jti and exp:
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        await blacklist_token(token_jti, expires_at, db)

    user_email = current_user.email
    user_id = current_user.id

    # Delete user from database (cascades to match_records and match_telemetry_points)
    await db.delete(current_user)
    await db.commit()

    logger.warning(f"User account permanently deleted: {user_email} (ID: {user_id})")
    return {
        "status": "account_deleted",
        "message": f"Account '{user_email}' and all associated match records have been permanently deleted.",
    }
