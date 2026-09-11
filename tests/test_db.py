"""
Unit tests for Database Engine, ORM Models, and Session Management.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, MatchRecord, MatchTelemetryPoint, TokenBlacklist, User


@pytest.fixture
def test_db_session():
    """Provides an isolated in-memory SQLite async database session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def setup_and_teardown():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        async with session_factory() as session:
            yield session
            
        await engine.dispose()

    gen = setup_and_teardown()
    return gen


class TestDatabaseModels:
    def test_create_user_and_match_cascade(self, test_db_session):
        async def run_test():
            session: AsyncSession = await anext(test_db_session)

            # 1. Create a User
            user = User(
                email="faker@t1.gg",
                username="Faker",
                hashed_password="mock_hashed_bcrypt_password",
                auth_provider="local",
                summoner_name="Hide on bush",
                region="kr",
                preferred_roles="MID",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            assert user.id is not None
            assert user.email == "faker@t1.gg"
            assert user.auth_provider == "local"

            # 2. Create a MatchRecord linked to User
            match = MatchRecord(
                user_id=user.id,
                champion_name="Azir",
                role="MID",
                kills=7,
                deaths=1,
                assists=8,
                cs=285,
                gold_earned=14500,
                duration_seconds=1800,
                win=True,
                tilt_triggers_count=0,
            )
            session.add(match)
            await session.commit()
            await session.refresh(match)

            assert match.id is not None
            assert match.user_id == user.id

            # 3. Add Telemetry Points via relationship
            point1 = MatchTelemetryPoint(
                game_time_seconds=300.0,
                cs=42,
                cs_per_minute=8.4,
                kills=1,
                deaths=0,
                flash_ready=True,
            )
            point2 = MatchTelemetryPoint(
                game_time_seconds=600.0,
                cs=95,
                cs_per_minute=9.5,
                kills=3,
                deaths=0,
                flash_ready=False,
                advice_text="Preserva Flash para el Dragón",
            )
            match.telemetry_points.extend([point1, point2])
            await session.commit()
            await session.refresh(match)

            assert len(match.telemetry_points) == 2
            assert match.telemetry_points[0].cs == 42
            assert match.telemetry_points[1].cs == 95

        asyncio.run(run_test())

    def test_token_blacklist(self, test_db_session):
        async def run_test():
            session: AsyncSession = await anext(test_db_session)

            blacklist_entry = TokenBlacklist(
                token_jti="unique_jwt_uuid_12345",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            session.add(blacklist_entry)
            await session.commit()

            stmt = select(TokenBlacklist).where(TokenBlacklist.token_jti == "unique_jwt_uuid_12345")
            result = await session.execute(stmt)
            entry = result.scalar_one_or_none()

            assert entry is not None
            assert entry.token_jti == "unique_jwt_uuid_12345"

        asyncio.run(run_test())
