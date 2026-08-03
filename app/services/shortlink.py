"""Shortlink service for monetizing free users."""

from __future__ import annotations

import httpx
from sqlalchemy import select
from app.database import get_session
from app.models import BotSetting
from app.utils.logger import logger

async def get_shortlink(original_url: str) -> str:
    async with get_session() as session:
        enabled_res = await session.execute(select(BotSetting).where(BotSetting.key == "shortlink_enabled"))
        enabled = enabled_res.scalar_one_or_none()
        
        if not enabled or enabled.value == "false":
            return original_url
            
        url_res = await session.execute(select(BotSetting).where(BotSetting.key == "shortlink_api_url"))
        api_url = url_res.scalar_one_or_none()
        
        key_res = await session.execute(select(BotSetting).where(BotSetting.key == "shortlink_api_key"))
        api_key = key_res.scalar_one_or_none()
        
        if not api_url or not api_key or not api_url.value or not api_key.value:
            return original_url
            
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                api_url.value,
                params={"api": api_key.value, "url": original_url}
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
