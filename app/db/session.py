"""
LolAnalyzer Backend - Database Session and Engine Configuration
Provides async SQLAlchemy engine and session factory supporting PostgreSQL in the cloud
and local SQLite fallback for isolated testing and development.
"""

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger("lol_analyzer.db")


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""
    pass


def get_engine() -> AsyncEngine:
    """Creates and configures the asynchronous SQLAlchemy engine."""
    db_url = settings.async_database_url
    is_sqlite = db_url.startswith("sqlite")

    engine_kwargs = {
        "echo": settings.DB_ECHO,
        "future": True,
    }

    if is_sqlite:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # PostgreSQL Connection Pooling
        engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
        engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
        engine_kwargs["pool_pre_ping"] = True

    logger.info(f"Initializing Async Database Engine: {'SQLite' if is_sqlite else 'PostgreSQL'}")
    return create_async_engine(db_url, **engine_kwargs)


engine = get_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an asynchronous database session.
    Automatically handles commit on success and rollback on exception.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initializes database schema and tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialized.")
