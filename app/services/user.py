"""User CRUD, premium management, and daily limit helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional, List
import re
import math

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import SearchLog, User, BotSetting, Bounty
from app.database import get_session
from app.utils.logger import logger

def get_level(aura: int) -> int:
    """Calculates dynamic XP Level based on Aura points."""
    if aura <= 0: return 0
    return math.floor(0.1 * math.sqrt(aura))

async def get_global_rank(telegram_id: int) -> int:
    """Calculates the user's global rank based on how many users have more Aura."""
    async with get_session() as session:
        user_res = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = user_res.scalar_one_or_none()
        if not user: return 0
        
        count_res = await session.execute(select(func.count(User.id)).where(User.aura > user.aura))
        higher_count = count_res.scalar() or 0
        return higher_count + 1

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
        res = await session.execute(select(BotSetting).where(BotSetting.key.in_([
            "premium_enabled", "free_search_limit", "premium_search_limit", "referral_reward_type", "referral_reward_amount"
        ])))
        settings_dict = {row.key: row.value for row in res.scalars().all()}

        prem_enabled = (settings_dict.get("premium_enabled") or "true") == "true"
        r_type = settings_dict.get("referral_reward_type") or "searches"
        r_amount_val = settings_dict.get("referral_reward_amount") or "0"
        r_amount = int(r_amount_val) if r_amount_val and r_amount_val.isdigit() else 0
        
        ref_bonus = (r_amount * user.referral_count) if r_type == "searches" else 0
        perm_bonus = getattr(user, "perm_search_bonus", 0) or 0

        if not prem_enabled:
            base_val = settings_dict.get("free_search_limit") or str(settings.FREE_SEARCH_LIMIT)
            return int(base_val) + ref_bonus + perm_bonus
        
        if user.is_premium:
            base_val = settings_dict.get("premium_search_limit") or str(settings.PREMIUM_SEARCH_LIMIT)
        else:
            base_val = settings_dict.get("free_search_limit") or str(settings.FREE_SEARCH_LIMIT)
        
        return int(base_val) + ref_bonus + perm_bonus

async def get_user_upload_limit(user: User) -> int:
    """Uploads are now completely unlimited. Returns a massive number."""
    return 999999

async def check_search_limit(user: User) -> bool:
    return user.search_count < await get_user_search_limit(user)

async def check_upload_limit(user: User) -> bool:
    return user.upload_count < await get_user_upload_limit(user)

async def increment_search_count(telegram_id: int) -> None:
    try:
        async with get_session() as session:
            await session.execute(
                text("UPDATE users SET search_count = search_count + 1 WHERE telegram_id = :tid"),
                {"tid": telegram_id}
            )
    except Exception as e:
        logger.error("increment_search_error", error=str(e))

async def increment_upload_count(telegram_id: int) -> None:
    try:
        async with get_session() as session:
            await session.execute(
                text("UPDATE users SET upload_count = upload_count + 1 WHERE telegram_id = :tid"),
                {"tid": telegram_id}
            )
    except Exception as e:
        logger.error("increment_upload_error", error=str(e))

async def add_aura(telegram_id: int, amount: int = 1):
    try:
        async with get_session() as session:
            await session.execute(
                text("UPDATE users SET aura = aura + :amt WHERE telegram_id = :tid"),
                {"tid": telegram_id, "amt": amount}
            )
    except Exception as e:
        logger.error("add_aura_error", error=str(e))

async def deduct_aura(telegram_id: int, amount: int) -> bool:
    """Charges the user's Aura balance. Returns True if successful, False if insufficient balance."""
    try:
        async with get_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            user = result.scalar_one_or_none()
            if not user or user.aura < amount:
                return False
            user.aura -= amount
            await session.flush()
            return True
    except Exception as e:
        logger.error("deduct_aura_error", error=str(e))
        return False

async def grant_bonus_searches(telegram_id: int, amount: int):
    """Gives the user instant extra searches for today by reducing their used search count."""
    try:
        async with get_session() as session:
            await session.execute(
                text("UPDATE users SET search_count = GREATEST(0, search_count - :amt) WHERE telegram_id = :tid"),
                {"tid": telegram_id, "amt": amount}
            )
    except Exception as e:
        logger.error("grant_bonus_searches_error", error=str(e))

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
            user.aura += 10 # Award 10 Aura for a referral!
            
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

async def get_leaderboard() -> List[User]:
    async with get_session() as session:
        res = await session.execute(select(User).where(User.aura > 0).order_by(User.aura.desc()).limit(10))
        return res.scalars().all()

async def check_bounty_match(session: AsyncSession, file_name: str, uploader_id: int) -> Optional[Bounty]:
    res = await session.execute(select(Bounty).where(Bounty.fulfilled == False, Bounty.requester_id != uploader_id))
    active_bounties = res.scalars().all()
    
    file_lower = file_name.lower()
    for bounty in active_bounties:
        query_words = [w.lower() for w in bounty.query.split() if len(w) > 2]
        if not query_words: continue
        
        matches = sum(1 for w in query_words if w in file_lower)
        match_percentage = matches / len(query_words)
        
        if match_percentage >= 0.7:
            return bounty
    return None

def auto_tag_file(file_name: str) -> dict:
    """Extremely Smart AI Auto-Tagger."""
    tags = {"subject": None, "category": None, "class_name": None, "year": None}
    
    # Year Extraction
    year_match = re.search(r'(20\d{2})', file_name)
    if year_match: tags["year"] = int(year_match.group(1))
    
    # Class Extraction
    class_match = re.search(r'(?:class|cls|grade|sem|semester)[\s_-]*(\d{1,2})', file_name, re.I)
    if class_match: tags["class_name"] = f"Class {class_match.group(1)}"
    
    # Category Extraction (Expanded)
    if re.search(r'pyq|previous year|question paper|solved|unsolved|sample', file_name, re.I): tags["category"] = "PYQ"
    elif re.search(r'notes|guide|handbook|summary|cheat|formula|module', file_name, re.I): tags["category"] = "Notes"
    elif re.search(r'book|textbook|novel', file_name, re.I): tags["category"] = "Book"
    elif re.search(r'assignment|lab|manual|experiment', file_name, re.I): tags["category"] = "Assignment"
    elif re.search(r'solution|answer|key', file_name, re.I): tags["category"] = "Solutions"
    elif re.search(r'slide|ppt|presentation', file_name, re.I): tags["category"] = "Slides"
    
    # Subject Extraction (Massively Expanded)
    subjects = [
        "physics", "chemistry", "math", "maths", "mathematics", "biology", "english", "hindi", 
        "history", "geography", "civics", "economics", "political", "science", "computer", "cs", 
        "it", "electronics", "mechanical", "civil", "electrical", "commerce", "accounts", 
        "business", "law", "arts", "jee", "neet", "upsc", "ssc", "gate", "cat", "mat", "zoology", "botany", "toefl", "ielts"
    ]
    file_lower = file_name.lower()
    for sub in subjects:
        if sub in file_lower:
            tags["subject"] = sub.capitalize()
            break
            
    return tags

async def get_stats(session: AsyncSession) -> dict:
    from app.models import Document
    total_docs = (await session.execute(select(func.count(Document.id)))).scalar() or 0
    total_users = (await session.execute(select(func.count(User.id)))).scalar() or 0
    premium_users = (await session.execute(select(func.count(User.id)).where(User.is_premium == True))).scalar() or 0
    pending_docs = (await session.execute(select(func.count(Document.id)).where(Document.approved == False))).scalar() or 0
    today = date.today()
    searches_today = (await session.execute(select(func.count(SearchLog.id)).where(func.date(SearchLog.created_at) == today))).scalar() or 0
    uploads_today = (await session.execute(select(func.count(Document.id)).where(func.date(Document.created_at) == today))).scalar() or 0
    
    active_bounties = (await session.execute(select(func.count(Bounty.id)).where(Bounty.fulfilled == False))).scalar() or 0
    active_buddies = (await session.execute(select(func.count(User.id)).where(User.study_buddy_subject != None))).scalar() or 0
    
    return {
        "total_documents": total_docs, "total_users": total_users, "premium_users": premium_users,
        "searches_today": searches_today, "pending_documents": pending_docs, "uploads_today": uploads_today,
        "active_bounties": active_bounties, "active_buddies": active_buddies
    }

async def log_search(session: AsyncSession, user_id: Optional[int], query: str, result_count: int) -> None:
    try:
        log = SearchLog(user_id=user_id, query=query, result_count=result_count)
        session.add(log)
        await session.flush()
    except Exception:
        await session.rollback()
