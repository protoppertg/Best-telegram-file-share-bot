"""Shortlink service for monetizing free users (Standby Feature)."""

from __future__ import annotations

import httpx
from app.config import settings
from app.utils.logger import logger

async def get_shortlink(original_url: str) -> str:
    """
    Generates a short link using the configured API.
    If SHORTLINK_ENABLED is False, returns the original URL.
    """
    if not settings.SHORTLINK_ENABLED or not settings.SHORTLINK_API_KEY:
        return original_url
        
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                settings.SHORTLINK_API_URL,
                params={"api": settings.SHORTLINK_API_KEY, "url": original_url}
            )
            data = resp.json()
            if data.get("status") == "success":
                return data.get("shortenedurl")
            else:
                logger.error("shortlink_failed", response=data)
                return original_url
    except Exception as exc:
        logger.error("shortlink_exception", error=str(exc))
        return original_url
