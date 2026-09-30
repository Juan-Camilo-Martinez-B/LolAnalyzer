"""
Official Riot Games API client.

Development and personal keys use X-Riot-Token on the server.
Riot Sign On is the production account-link flow, and it requires an approved
production application. This client never asks for a Riot password.
Public match payloads are cached with a TTL and are not written to match_records.
"""

from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import RiotCacheEntry

logger = logging.getLogger("lol_analyzer.riot")

PLATFORM_TO_REGION = {
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "oc1": "sea",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "kr": "asia",
    "jp1": "asia",
}

PROFILE_TTL = timedelta(minutes=10)
MATCH_TTL = timedelta(minutes=2)


class RiotUnavailable(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def require_api_key() -> str:
    key = (settings.RIOT_API_KEY or "").strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Riot API is not configured. Set RIOT_API_KEY on the server.",
        )
    return key


def regional_host(platform: str) -> str:
    cluster = PLATFORM_TO_REGION.get(platform.lower())
    if not cluster:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported region.")
    return cluster


async def _cached(db: AsyncSession, key: str) -> Any | None:
    now = datetime.now(timezone.utc)
    result = await db.execute(select(RiotCacheEntry).where(RiotCacheEntry.cache_key == key))
    row = result.scalar_one_or_none()
    if row and row.expires_at > now:
        return json.loads(row.payload)
    return None


async def _store(db: AsyncSession, key: str, payload: Any, ttl: timedelta) -> None:
    now = datetime.now(timezone.utc)
    encoded = json.dumps(payload)
    result = await db.execute(select(RiotCacheEntry).where(RiotCacheEntry.cache_key == key))
    row = result.scalar_one_or_none()
    if row is None:
        db.add(RiotCacheEntry(cache_key=key, payload=encoded, expires_at=now + ttl))
    else:
        row.payload = encoded
        row.expires_at = now + ttl
    await db.commit()


async def _request(method_url: str) -> Any:
    key = require_api_key()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(method_url, headers={"X-Riot-Token": key})
    except httpx.HTTPError as exc:
        logger.info("Riot API network error: %s", type(exc).__name__)
        raise RiotUnavailable(status.HTTP_503_SERVICE_UNAVAILABLE, "Riot API is temporarily unavailable.") from exc

    if response.status_code == 404:
        raise RiotUnavailable(status.HTTP_404_NOT_FOUND, "Riot account or resource was not found.")
    if response.status_code == 429:
        raise RiotUnavailable(status.HTTP_429_TOO_MANY_REQUESTS, "Riot API rate limit reached. Try again shortly.")
    if response.status_code in {401, 403}:
        logger.info("Riot API rejected the server key with status %s", response.status_code)
        raise RiotUnavailable(status.HTTP_503_SERVICE_UNAVAILABLE, "Riot API key was rejected.")
    if response.status_code >= 500:
        raise RiotUnavailable(status.HTTP_502_BAD_GATEWAY, "Riot API returned an upstream error.")
    if response.status_code >= 400:
        raise RiotUnavailable(status.HTTP_502_BAD_GATEWAY, "Riot API rejected the request.")
    return response.json()


def raise_riot(error: RiotUnavailable) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message)


async def account_by_riot_id(db: AsyncSession, game_name: str, tag_line: str, platform: str) -> dict[str, Any]:
    cluster = regional_host(platform)
    cache_key = f"account:{cluster}:{game_name.lower()}:{tag_line.lower()}"
    cached = await _cached(db, cache_key)
    if cached:
        return cached
    url = (
        f"https://{cluster}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
        f"{quote(game_name)}/{quote(tag_line)}"
    )
    try:
        payload = await _request(url)
    except RiotUnavailable:
        raise
    await _store(db, cache_key, payload, PROFILE_TTL)
    return payload


async def summoner_by_puuid(db: AsyncSession, platform: str, puuid: str) -> dict[str, Any]:
    cache_key = f"summoner:{platform}:{puuid}"
    cached = await _cached(db, cache_key)
    if cached:
        return cached
    url = f"https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{quote(puuid)}"
    payload = await _request(url)
    await _store(db, cache_key, payload, PROFILE_TTL)
    return payload


async def league_entries(db: AsyncSession, platform: str, puuid: str) -> list[dict[str, Any]]:
    cache_key = f"league:{platform}:{puuid}"
    cached = await _cached(db, cache_key)
    if cached is not None:
        return cached
    url = f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-puuid/{quote(puuid)}"
    payload = await _request(url)
    if not isinstance(payload, list):
        payload = []
    await _store(db, cache_key, payload, PROFILE_TTL)
    return payload


async def recent_match_ids(db: AsyncSession, platform: str, puuid: str, count: int) -> list[str]:
    cluster = regional_host(platform)
    cache_key = f"match-ids:{cluster}:{puuid}:{count}"
    cached = await _cached(db, cache_key)
    if cached is not None:
        return cached
    url = (
        f"https://{cluster}.api.riotgames.com/lol/match/v5/matches/by-puuid/"
        f"{quote(puuid)}/ids?start=0&count={count}"
    )
    payload = await _request(url)
    if not isinstance(payload, list):
        payload = []
    await _store(db, cache_key, payload, MATCH_TTL)
    return payload


async def match_detail(db: AsyncSession, platform: str, match_id: str) -> dict[str, Any]:
    cluster = regional_host(platform)
    cache_key = f"match:{cluster}:{match_id}"
    cached = await _cached(db, cache_key)
    if cached:
        return cached
    url = f"https://{cluster}.api.riotgames.com/lol/match/v5/matches/{quote(match_id)}"
    payload = await _request(url)
    await _store(db, cache_key, payload, MATCH_TTL)
    return payload
