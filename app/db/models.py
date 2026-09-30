"""
LolAnalyzer Backend - Database ORM Models
Defines PostgreSQL/SQLite database models for Users, Token Blacklist,
Match History, and Granular In-Game Telemetry Series for visual charting.
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def utc_now() -> datetime:
    """Returns current UTC timestamp with timezone awareness."""
    return datetime.now(timezone.utc)


class User(Base):
    """
    User entity supporting hybrid authentication (Local Bcrypt & Google OAuth2),
    profile customization, and League of Legends account linkage.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(50), default="local", nullable=False)  # "local" | "google"
    google_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # LoL Summoner Linkage (via LCU)
    summoner_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    summoner_icon_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    region: Mapped[str] = mapped_column(String(20), default="la1", nullable=False)
    
    # Coach Preferences
    preferred_roles: Mapped[str] = mapped_column(String(100), default="MID,TOP", nullable=False)
    coach_sensitivity: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)  # "low", "normal", "high"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Official Riot account link. Only identifiers; match data stays on Riot's API.
    riot_puuid: Mapped[Optional[str]] = mapped_column(String(80), unique=True, index=True, nullable=True)
    riot_game_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    riot_tag_line: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    matches: Mapped[List["MatchRecord"]] = relationship(
        "MatchRecord", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    security_answers: Mapped[Optional["SecurityAnswers"]] = relationship(
        "SecurityAnswers", back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class SecurityAnswers(Base):
    """
    Hashed recovery answers. The plaintext is never stored or returned.
    """
    __tablename__ = "security_answers"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    favorite_champion_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    peak_elo_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_main_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="security_answers")


class AuthAttempt(Base):
    """
    Shared lockout counter so login and recovery limits survive more than one process.
    The subject is a hash, not the raw email.
    """
    __tablename__ = "auth_attempts"
    __table_args__ = (UniqueConstraint("subject_hash", "purpose", name="uq_auth_attempt_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    window_started: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class RiotCacheEntry(Base):
    """Short-lived Riot API cache. Rows expire by TTL and are not a match archive."""
    __tablename__ = "riot_cache"

    cache_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TokenBlacklist(Base):
    """
    Stores revoked JWT IDs (jti) for immediate, cryptographically sound Logout.
    """
    __tablename__ = "token_blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    token_jti: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MatchRecord(Base):
    """
    Historical match entity recording player game outcomes, performance metrics,
    and AI coach interactions.
    """
    __tablename__ = "match_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    game_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    champion_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    kills: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deaths: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    assists: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    gold_earned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    gold_difference: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    win: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Tactical Coach Metrics
    tilt_triggers_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    advices_received_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    advices_followed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="matches")
    telemetry_points: Mapped[List["MatchTelemetryPoint"]] = relationship(
        "MatchTelemetryPoint", back_populates="match", cascade="all, delete-orphan", lazy="selectin"
    )


class MatchTelemetryPoint(Base):
    """
    Fine-grained time-series telemetry points for rendering interactive visual charts
    (CS/min progression vs ideal benchmark, death timestamps, Flash availability).
    """
    __tablename__ = "match_telemetry_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("match_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    game_time_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    cs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cs_per_minute: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    kills: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deaths: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    flash_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    advice_text: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    match: Mapped["MatchRecord"] = relationship("MatchRecord", back_populates="telemetry_points")
