"""
LolAnalyzer Backend - Database Session and Engine Configuration
Provides async SQLAlchemy engine and session factory supporting PostgreSQL in the cloud
and local SQLite fallback for isolated testing and development.
"""

import logging
from contextlib import asynccontextmanager
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
    """Account and match data. Postgres when DATABASE_URL is set, else local SQLite."""
    pass


class CacheBase(DeclarativeBase):
    """Short-lived Riot cache. Always a separate local SQLite file."""
    pass


def _build_engine(db_url: str, *, pooled: bool) -> AsyncEngine:
    """Creates an async engine. Postgres uses the Supabase pooler settings."""
    is_sqlite = db_url.startswith("sqlite")
    engine_kwargs = {
        "echo": settings.DB_ECHO,
        "future": True,
    }

    if is_sqlite:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    elif pooled:
        # Supabase exposes PgBouncer in transaction mode, which rejects asyncpg's
        # named prepared statements. Both caches must stay off for that pooler.
        engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
        engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["connect_args"] = {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        }

    logger.info("Initializing async database engine: %s", "SQLite" if is_sqlite else "PostgreSQL")
    return create_async_engine(db_url, **engine_kwargs)


def get_engine() -> AsyncEngine:
    """Account engine: PostgreSQL when DATABASE_URL is set, otherwise local SQLite."""
    return _build_engine(settings.async_database_url, pooled=True)


def get_cache_engine() -> AsyncEngine:
    """Riot cache engine. Always SQLite, never the account database."""
    return _build_engine(settings.cache_database_url, pooled=False)


engine = get_engine()
cache_engine = get_cache_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

CacheSessionLocal = async_sessionmaker(
    bind=cache_engine,
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


@asynccontextmanager
async def cache_session() -> AsyncGenerator[AsyncSession, None]:
    """Short session for riot_cache. Commits do not touch the account database."""
    async with CacheSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_cache_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for the local Riot cache."""
    async with cache_session() as session:
        yield session


async def init_db() -> None:
    """Creates account tables on the main engine and riot_cache on local SQLite."""
    from app.db.schema_compat import ensure_auth_columns

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_auth_columns(conn)
    async with cache_engine.begin() as conn:
        await conn.run_sync(CacheBase.metadata.create_all)
    logger.info("Database schema initialized.")
