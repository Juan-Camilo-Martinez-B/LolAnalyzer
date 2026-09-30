"""
Database-backed attempt limiter for login and account recovery.
The subject is stored as a hash so logs and rows do not keep the raw email.
"""

from datetime import datetime, timedelta, timezone
import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuthAttempt

MAX_ATTEMPTS = 5
WINDOW = timedelta(minutes=15)
LOCK = timedelta(minutes=15)


def subject_hash(purpose: str, email: str) -> str:
    material = f"{purpose}:{email.strip().lower()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


async def _row(db: AsyncSession, digest: str, purpose: str) -> AuthAttempt | None:
    stmt = select(AuthAttempt).where(
        AuthAttempt.subject_hash == digest,
        AuthAttempt.purpose == purpose,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def is_locked(row: AuthAttempt | None, now: datetime) -> bool:
    return bool(row and row.locked_until and row.locked_until > now)


async def assert_not_locked(db: AsyncSession, email: str, purpose: str) -> None:
    from fastapi import HTTPException, status

    now = datetime.now(timezone.utc)
    row = await _row(db, subject_hash(purpose, email), purpose)
    if is_locked(row, now):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos. Espera unos minutos e inténtalo de nuevo.",
        )


async def register_failure(db: AsyncSession, email: str, purpose: str) -> None:
    now = datetime.now(timezone.utc)
    digest = subject_hash(purpose, email)
    row = await _row(db, digest, purpose)
    if row is None:
        row = AuthAttempt(subject_hash=digest, purpose=purpose, attempts=1, window_started=now)
        db.add(row)
    else:
        if row.window_started < now - WINDOW:
            row.attempts = 1
            row.window_started = now
            row.locked_until = None
        else:
            row.attempts += 1
        if row.attempts >= MAX_ATTEMPTS:
            row.locked_until = now + LOCK
    await db.commit()


async def clear_attempts(db: AsyncSession, email: str, purpose: str) -> None:
    row = await _row(db, subject_hash(purpose, email), purpose)
    if row is not None:
        await db.delete(row)
        await db.commit()
