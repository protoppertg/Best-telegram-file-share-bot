"""User CRUD, premium management, and daily limit helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import SearchLog, User, BotSetting
from app.database import get_session
from app.utils.logger import logger

async def get_or_create_user(session: AsyncSession, telegram_id: int, username: Optional[str] = None, first_name: Optional[str] = None, last_name: Optional[str] = None) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name, last_name=last_name)
        session.add(user)
        await session.flush()
    else:
        changed = False
        if username and user.username != username: user.username = username; changed = True
        if first_name and user.first_name != first_name: user.first_name = first_name; changed = True
        if last_name and user.last_name != last_name: user.last_name = last_name; changed = True
        if changed: await session.flush()
    return user

async def reset_daily_counts_if_needed(session: AsyncSession, user: User) -> bool:
    today = date.today()
    if user.last_reset_date is None or user.last_reset_date < today:
        user.search_count = 0
        user.upload_count = 0
        user.last_reset_date = today
        if user.is_premium and user.premium_expiry:
            if user.premium_expiry < datetime.now(timezone.utc):
                user.is_premium = False
                user.premium_expiry = None
        await session.flush()
        return True
    return False

async def get_user_search_limit(user: User) -> int:
    async with get_session() as session:
        # OPTIMIZED: Fetch all settings in one single DB query
        res = await session.execute(select(BotSetting).where(BotSetting.key.in_([
            "premium_enabled", "free_search_limit", "premium_search_limit", "referral_reward_type", "referral_reward_amount"
        ])))
        settings_dict = {row.key: row.value for row in res.scalars().all()}

        prem_enabled = (settings_dict.get("premium_enabled") or "true") == "true"
        r_type = settings_dict.get("referral_reward_type") or "searches"
        r_amount_val = settings_dict.get("referral_reward_amount") or "0"
        r_amount = int(r_amount_val) if r_amount_val and r_amount_val.isdigit() else 0
        
        ref_bonus = (r_amount * user.referral_count) if r_type == "searches" else 0

        if not prem_enabled:
            base_val = settings_dict.get("free_search_limit") or str(settings.FREE_SEARCH_LIMIT)
            return int(base_val) + ref_bonus
        
        if user.is_premium:
            base_val = settings_dict.get("premium_search_limit") or str(settings.PREMIUM_SEARCH_LIMIT)
        else:
            base_val = settings_dict.get("free_search_limit") or str(settings.FREE_SEARCH_LIMIT)
        
        return int(base_val) + ref_bonus

async def get_user_upload_limit(user: User) -> int:
    return settings.PREMIUM_UPLOAD_LIMIT if user.is_premium else settings.FREE_UPLOAD_LIMIT

async def check_search_limit(user: User) -> bool:
    return user.search_count < await get_user_search_limit(user)

async def check_upload_limit(user: User) -> bool:
    return user.upload_count < await get_user_upload_limit(user)

async def increment_search_count(telegram_id: int) -> None:
    # ULTRA-FAST ATOMIC UPDATE: No need to load the user object, just increment directly in DB
    try:
        async with get_session() as session:
            await session.execute(
                text("UPDATE users SET search_count = search_count + 1 WHERE telegram_id = :tid"),
                {"tid": telegram_id}
            )
    except Exception as e:
        logger.error("increment_search_error", error=str(e))

async def increment_upload_count(telegram_id: int) -> None:
    # ULTRA-FAST ATOMIC UPDATE: No need to load the user object, just increment directly in DB
    try:
        async with get_session() as session:
            await session.execute(
                text("UPDATE users SET upload_count = upload_count + 1 WHERE telegram_id = :tid"),
                {"tid": telegram_id}
            )
    except Exception as e:
        logger.error("increment_upload_error", error=str(e))

async def add_referral(telegram_id: int):
    async with get_session() as session:
        res = await session.execute(select(BotSetting).where(BotSetting.key.in_(["referral_reward_type", "referral_reward_amount"])))
        s_dict = {r.key: r.value for r in res.scalars().all()}
        r_type = s_dict.get("referral_reward_type") or "searches"
        r_amount_val = s_dict.get("referral_reward_amount") or "1"
        r_amount = int(r_amount_val) if r_amount_val and r_amount_val.isdigit() else 1

        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user:
            user.referral_count += 1
            
            if r_type == "premium":
                now = datetime.now(timezone.utc)
                base = user.premium_expiry if user.is_premium and user.premium_expiry and user.premium_expiry > now else now
                user.is_premium = True
                user.premium_expiry = base + timedelta(days=r_amount)
            elif r_type == "daily_bonus":
                user.search_count = max(0, user.search_count - r_amount)
                
            await session.flush()

async def activate_premium(telegram_id: int, duration_days: int) -> bool:
    async with get_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user: return False
        user.is_premium = True
        now = datetime.now(timezone.utc)
        base = user.premium_expiry if user.is_premium and user.premium_expiry and user.premium_expiry > now else now
        if duration_days == 0:
            user.premium_expiry = base + timedelta(days=36500)
        else:
            user.premium_expiry = base + timedelta(days=duration_days)
        await session.flush()
        return True

async def revoke_premium(telegram_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user: return False
        user.is_premium = False
        user.premium_expiry = None
        await session.flush()
        return True

async def ban_user(telegram_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user: return False
        user.is_banned = True
        await session.flush()
        return True

async def unban_user(telegram_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user: return False
        user.is_banned = False
        await session.flush()
        return True

async def reset_search_count(telegram_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user: return False
        user.search_count = 0
        await session.flush()
        return True

async def get_stats(session: AsyncSession) -> dict:
    from app.models import Document
    total_docs = (await session.execute(select(func.count(Document.id)))).scalar() or 0
    total_users = (await session.execute(select(func.count(User.id)))).scalar() or 0
    premium_users = (await session.execute(select(func.count(User.id)).where(User.is_premium == True))).scalar() or 0
    pending_docs = (await session.execute(select(func.count(Document.id)).where(Document.approved == False))).scalar() or 0
    today = date.today()
    searches_today = (await session.execute(select(func.count(SearchLog.id)).where(func.date(SearchLog.created_at) == today))).scalar() or 0
    uploads_today = (await session.execute(select(func.count(Document.id)).where(func.date(Document.created_at) == today))).scalar() or 0
    return {
        "total_documents": total_docs, "total_users": total_users, "premium_users": premium_users,
        "searches_today": searches_today, "pending_documents": pending_docs, "uploads_today": uploads_today,
    }

async def log_search(session: AsyncSession, user_id: Optional[int], query: str, result_count: int) -> None:
    try:
        log = SearchLog(user_id=user_id, query=query, result_count=result_count)
        session.add(log)
        await session.flush()
    except Exception:
        await session.rollback()
