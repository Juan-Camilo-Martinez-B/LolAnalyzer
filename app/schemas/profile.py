"""
LolAnalyzer Backend - Profile Schemas
Defines request and response validation contracts for profile inspection,
profile editing, password changes, LCU account sync, and secure account deletion.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class ProfileUpdate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=2, max_length=50)
    avatar_url: Optional[str] = None
    preferred_roles: Optional[str] = Field(default=None, description="Comma-separated roles, e.g. 'MID,TOP'")
    coach_sensitivity: Optional[str] = Field(default=None, description="'low', 'normal', 'high'")
    region: Optional[str] = Field(default=None, description="League region (e.g. la1, na1, euw1)")
    summoner_name: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., description="Current account password")
    new_password: str = Field(..., min_length=6, description="New password with minimum 6 characters")


class DeleteAccountRequest(BaseModel):
    password: Optional[str] = Field(default=None, description="Current password for verification (required for local accounts)")
    confirmation_text: str = Field(default="DELETE", description="Confirmation string 'DELETE'")


class ProfileResponse(BaseModel):
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
    lcu_live_connected: bool = False
    total_matches_recorded: int = 0
