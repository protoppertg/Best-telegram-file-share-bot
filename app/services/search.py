"""PostgreSQL full-text search service (Pure ORM - Bulletproof)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Document
from app.services.cache import get_cache
from app.utils.logger import logger

@dataclass
class SearchRow:
    id: int
    file_name: str
    subject: Optional[str]
    category: Optional[str]
    class_name: Optional[str]
    year: Optional[int]

async def search_documents(
    session: AsyncSession, 
    query: str, 
    page: int = 1, 
    per_page: Optional[int] = None,
    subject: Optional[str] = None,
    class_name: Optional[str] = None,
    year: Optional[int] = None
) -> tuple[List[SearchRow], int]:
    per_page = per_page or settings.SEARCH_RESULTS_PER_PAGE
    offset = (page - 1) * per_page
    
    words = [w for w in query.strip().split() if w]
    if not words:
        words = [""]

    stmt = select(Document).where(Document.approved == True)
    count_stmt = select(func.count(Document.id)).where(Document.approved == True)

    for word in words:
        word_filter = or_(
            Document.file_name.ilike(f"%{word}%"),
            Document.subject.ilike(f"%{word}%"),
            Document.category.ilike(f"%{word}%"),
            Document.class_name.ilike(f"%{word}%")
        )
        stmt = stmt.where(word_filter)
        count_stmt = count_stmt.where(word_filter)

    if subject:
        stmt = stmt.where(Document.subject.ilike(f"%{subject}%"))
        count_stmt = count_stmt.where(Document.subject.ilike(f"%{subject}%"))
    
    if class_name:
        stmt = stmt.where(Document.class_name.ilike(f"%{class_name}%"))
        count_stmt = count_stmt.where(Document.class_name.ilike(f"%{class_name}%"))
        
    if year:
        stmt = stmt.where(Document.year == year)
        count_stmt = count_stmt.where(Document.year == year)

    first_word = words[0]
    stmt = stmt.order_by(
        Document.file_name.ilike(f"{first_word}%").desc(),
        Document.created_at.desc()
    )

    stmt = stmt.offset(offset).limit(per_page)

    cache_key = f"search:{query}:{page}:{subject}:{class_name}:{year}"
    
    cache = await get_cache()
    cached = await cache.get(cache_key)
    if cached:
        return [SearchRow(**r) for r in cached["rows"]], cached["total"]

    try:
        result = await session.execute(stmt)
        docs = result.scalars().all()
        
        count_result = await session.execute(count_stmt)
        total = count_result.scalar() or 0

        rows = [{"id": d.id, "file_name": d.file_name, "subject": d.subject, "category": d.category, "class_name": d.class_name, "year": d.year} for d in docs]
        await cache.set(cache_key, {"rows": rows, "total": total}, ttl=settings.CACHE_TTL_SECONDS)
        return [SearchRow(**r) for r in rows], total
    except Exception as e:
        logger.error("search_database_error", error=str(e), exc_info=True)
        await session.rollback()
        return [], 0
