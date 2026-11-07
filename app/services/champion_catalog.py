"""
Champion catalog from Data Dragon, with a bundled fallback when the CDN is down.
The list is cached. It is not account data.
"""

from datetime import datetime, timedelta, timezone
import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.recovery import BUNDLED_CHAMPIONS
from app.db.models import RiotCacheEntry
from app.db.session import cache_session

logger = logging.getLogger("lol_analyzer.champions")

CACHE_KEY = "ddragon:champions"
TTL = timedelta(hours=24)


def _aware(value: datetime) -> datetime:
    # SQLite devuelve fechas sin zona horaria aunque la columna la declare.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def bundled_catalog() -> list[dict[str, str]]:
    return [{"id": champion_id, "name": name} for champion_id, name in BUNDLED_CHAMPIONS]


async def get_champion_catalog(db: AsyncSession | None = None) -> list[dict[str, str]]:
    """Load the champion list from the local Riot cache, not the account database.

    Routes still pass the account session. It is unused so a cache write cannot
    commit or roll back users, security answers, or match history.
    """
    _ = db
    now = datetime.now(timezone.utc)
    async with cache_session() as cache:
        cached = await cache.execute(select(RiotCacheEntry).where(RiotCacheEntry.cache_key == CACHE_KEY))
        row = cached.scalar_one_or_none()
        if row and _aware(row.expires_at) > now:
            try:
                payload = json.loads(row.payload)
                if isinstance(payload, list) and payload:
                    return payload
            except json.JSONDecodeError:
                pass

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                versions = await client.get("https://ddragon.leagueoflegends.com/api/versions.json")
                versions.raise_for_status()
                version = versions.json()[0]
                response = await client.get(
                    f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
                )
                response.raise_for_status()
                raw = response.json().get("data", {})
            catalog = sorted(
                ({"id": key, "name": value.get("name", key)} for key, value in raw.items()),
                key=lambda item: item["name"].lower(),
            )
        except Exception as exc:
            logger.info("Data Dragon champion list unavailable, using bundled catalog: %s", type(exc).__name__)
            return bundled_catalog()

        payload = json.dumps(catalog)
        if row is None:
            cache.add(RiotCacheEntry(cache_key=CACHE_KEY, payload=payload, expires_at=now + TTL))
        else:
            row.payload = payload
            row.expires_at = now + TTL
        return catalog
