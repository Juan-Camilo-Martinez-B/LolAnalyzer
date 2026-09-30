"""
LolAnalyzer Backend - Authentication & User Schemas
Defines request and response validation contracts for registration, login,
Google OAuth, JWT tokens, and user profile representations.
"""

from datetime import datetime
import re
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class UserRegister(BaseModel):
    email: str = Field(..., description="Valid email address")
    username: str = Field(..., min_length=2, max_length=50, description="Display name")
    password: str = Field(..., min_length=8, description="Password with minimum 8 characters")
    password_confirm: str = Field(..., min_length=8)
    favorite_champion: str = Field(..., min_length=1, max_length=40)
    peak_elo: str = Field(..., min_length=1, max_length=40)
    first_main: str = Field(..., min_length=1, max_length=40)
    summoner_name: Optional[str] = Field(default=None, description="Optional LoL summoner name")
    region: str = Field(default="la1", description="League region (e.g. la1, na1, euw1)")
    preferred_roles: str = Field(default="MID,TOP", description="Comma-separated preferred roles")
    coach_sensitivity: str = Field(default="normal", description="Coach sensitivity: low, normal, high")

    @field_validator("email")
    @classmethod
    def email_shape(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cleaned):
            raise ValueError("Invalid email address")
        return cleaned

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.password_confirm:
            raise ValueError("Passwords do not match")
        return self



class UserLogin(BaseModel):
    email: str
    password: str


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(..., description="Google ID Token obtained from Google Sign-In button")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_seconds: int


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class RecoveryResetRequest(BaseModel):
    email: str
    favorite_champion: str = Field(..., min_length=1, max_length=40)
    peak_elo: str = Field(..., min_length=1, max_length=40)
    first_main: str = Field(..., min_length=1, max_length=40)
    new_password: str = Field(..., min_length=8)
    new_password_confirm: str = Field(..., min_length=8)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.new_password_confirm:
            raise ValueError("Passwords do not match")
        return self


class RecoveryStartRequest(BaseModel):
    email: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    username: str
    auth_provider: str
    avatar_url: Optional[str] = None
    summoner_name: Optional[str] = None
    summoner_icon_id: Optional[int] = None
    region: str
    preferred_roles: str
    coach_sensitivity: str
    riot_game_name: Optional[str] = None
    riot_tag_line: Optional[str] = None
    riot_linked: bool = False
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def mark_riot_link(cls, value):
        if isinstance(value, dict):
            value.setdefault("riot_linked", bool(value.get("riot_puuid")))
            return value
        return {
            "id": value.id,
            "email": value.email,
            "username": value.username,
            "auth_provider": value.auth_provider,
            "avatar_url": value.avatar_url,
            "summoner_name": value.summoner_name,
            "summoner_icon_id": value.summoner_icon_id,
            "region": value.region,
            "preferred_roles": value.preferred_roles,
            "coach_sensitivity": value.coach_sensitivity,
            "riot_game_name": getattr(value, "riot_game_name", None),
            "riot_tag_line": getattr(value, "riot_tag_line", None),
            "riot_linked": bool(getattr(value, "riot_puuid", None)),
            "created_at": value.created_at,
        }


class LogoutResponse(BaseModel):
    status: str = "logged_out"
    message: str
