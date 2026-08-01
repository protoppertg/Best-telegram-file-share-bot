"""PostgreSQL full-text search service (Advanced Filter & Fuzzy Version)."""

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
        d.file_name ILIKE :q OR 
        coalesce(d.subject, '') ILIKE :q OR
        coalesce(d.category, '') ILIKE :q OR
        coalesce(d.class_name, '') ILIKE :q
    )
    AND (:subject IS NULL OR d.subject ILIKE '%' || :subject || '%')
    AND (:class_name IS NULL OR d.class_name ILIKE '%' || :class_name || '%')
    AND (:year IS NULL OR d.year = :year)
    ORDER BY 
        CASE WHEN d.file_name ILIKE :q_start THEN 0 ELSE 1 END,
        d.created_at DESC
    LIMIT :limit OFFSET :offset
""")

COUNT_SQL = text("""
    SELECT COUNT(*) FROM documents d
    WHERE d.approved = true AND (
        d.file_name ILIKE :q OR 
        coalesce(d.subject, '') ILIKE :q OR
        coalesce(d.category, '') ILIKE :q OR
        coalesce(d.class_name, '') ILIKE :q
    )
    AND (:subject IS NULL OR d.subject ILIKE '%' || :subject || '%')
    AND (:class_name IS NULL OR d.class_name ILIKE '%' || :class_name || '%')
    AND (:year IS NULL OR d.year = :year)
""")

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
    
    # Fuzzy matching: replace spaces with '%' so "physics thermodynamics" becomes "%physics%thermodynamics%"
    # This allows it to match "physics_notes_thermodynamics.pdf"
    normalized = query.strip()
    fuzzy_q = '%' + '%'.join(normalized.split()) + '%'
    fuzzy_q_start = normalized.split()[0] + '%' if normalized.split() else '%%'
    
    cache_key = f"search:{normalized}:{page}:{subject}:{class_name}:{year}"
    
    cache = await get_cache()
    cached = await cache.get(cache_key)
    if cached:
        return [SearchRow(**r) for r in cached["rows"]], cached["total"]

    try:
        params = {
            "q": fuzzy_q, 
            "q_start": fuzzy_q_start,
            "limit": per_page, 
            "offset": offset,
            "subject": subject,
            "class_name": class_name,
            "year": year
        }
        result = await session.execute(SEARCH_SQL, params)
        raw_rows = result.fetchall()
        
        count_result = await session.execute(COUNT_SQL, params)
        total = count_result.scalar() or 0

        rows = [{"id": r.id, "file_name": r.file_name, "subject": r.subject, "category": r.category, "class_name": r.class_name, "year": r.year} for r in raw_rows]
        await cache.set(cache_key, {"rows": rows, "total": total}, ttl=settings.CACHE_TTL_SECONDS)
        return [SearchRow(**r) for r in rows], total
    except Exception as e:
        logger.error("search_database_error", error=str(e), exc_info=True)
        await session.rollback()
        return [], 0
