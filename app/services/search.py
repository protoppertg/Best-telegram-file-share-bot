"""PostgreSQL full-text search service (Bulletproof Version)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
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

SEARCH_SQL = text("""
    SELECT d.id, d.file_name, d.subject, d.category, d.class_name, d.year
    FROM documents d
    WHERE d.approved = true AND (
        d.file_name ILIKE '%' || :q || '%' OR 
        coalesce(d.subject, '') ILIKE '%' || :q || '%' OR
        coalesce(d.category, '') ILIKE '%' || :q || '%' OR
        coalesce(d.class_name, '') ILIKE '%' || :q || '%'
    )
    ORDER BY 
        CASE WHEN d.file_name ILIKE :q || '%' THEN 0 ELSE 1 END,
        d.created_at DESC
    LIMIT :limit OFFSET :offset
""")

COUNT_SQL = text("""
    SELECT COUNT(*) FROM documents d
    WHERE d.approved = true AND (
        d.file_name ILIKE '%' || :q || '%' OR 
        coalesce(d.subject, '') ILIKE '%' || :q || '%' OR
        coalesce(d.category, '') ILIKE '%' || :q || '%' OR
        coalesce(d.class_name, '') ILIKE '%' || :q || '%'
    )
""")

async def search_documents(session: AsyncSession, query: str, page: int = 1, per_page: Optional[int] = None) -> tuple[List[SearchRow], int]:
    per_page = per_page or settings.SEARCH_RESULTS_PER_PAGE
    offset = (page - 1) * per_page
    normalized = query.strip()
    cache_key = f"search:{normalized}:{page}"
    
    cache = await get_cache()
    cached = await cache.get(cache_key)
    if cached:
        return [SearchRow(**r) for r in cached["rows"]], cached["total"]

    try:
        result = await session.execute(SEARCH_SQL, {"q": normalized, "limit": per_page, "offset": offset})
        raw_rows = result.fetchall()
        count_result = await session.execute(COUNT_SQL, {"q": normalized})
        total = count_result.scalar() or 0

        rows = [{"id": r.id, "file_name": r.file_name, "subject": r.subject, "category": r.category, "class_name": r.class_name, "year": r.year} for r in raw_rows]
        await cache.set(cache_key, {"rows": rows, "total": total}, ttl=settings.CACHE_TTL_SECONDS)
        return [SearchRow(**r) for r in rows], total
    except Exception as e:
        logger.error("search_database_error", error=str(e), exc_info=True)
        await session.rollback()
        return [], 0
