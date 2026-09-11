"""
LolAnalyzer Backend - Authentication & User Schemas
Defines request and response validation contracts for registration, login,
Google OAuth, JWT tokens, and user profile representations.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class UserRegister(BaseModel):
    email: str = Field(..., description="Valid email address")
    username: str = Field(..., min_length=2, max_length=50, description="Display name")
    password: str = Field(..., min_length=6, description="Password with minimum 6 characters")
    summoner_name: Optional[str] = Field(default=None, description="Optional LoL summoner name")
    region: str = Field(default="la1", description="League region (e.g. la1, na1, euw1)")


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
    created_at: datetime


class LogoutResponse(BaseModel):
    status: str = "logged_out"
    message: str
