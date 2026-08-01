"""PostgreSQL full-text search service (Smart Word-Splitting Version)."""

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
    
    # Split the query into individual words
    words = [w for w in query.strip().split() if w]
    if not words:
        words = [""] # Fallback if only filters are used

    # Dynamically build the SQL so every word must exist somewhere in the text
    where_clauses = []
    params = {
        "limit": per_page, 
        "offset": offset,
        "subject": subject,
        "class_name": class_name,
        "year": year,
        "q_start": words[0] + "%" # Used to prioritize files starting with the first word
    }

    for i, word in enumerate(words):
        param_name = f"w_{i}"
        params[param_name] = f"%{word}%"
        where_clauses.append(f"""(
            d.file_name ILIKE :{param_name} OR 
            coalesce(d.subject, '') ILIKE :{param_name} OR
            coalesce(d.category, '') ILIKE :{param_name} OR
            coalesce(d.class_name, '') ILIKE :{param_name}
        )""")
    
    # Join with AND so ALL words must be present, but in any order
    word_filter = " AND ".join(where_clauses)

    SEARCH_SQL = text(f"""
        SELECT d.id, d.file_name, d.subject, d.category, d.class_name, d.year
        FROM documents d
        WHERE d.approved = true AND ({word_filter})
        AND (:subject IS NULL OR d.subject ILIKE '%' || :subject || '%')
        AND (:class_name IS NULL OR d.class_name ILIKE '%' || :class_name || '%')
        AND (:year IS NULL OR d.year = :year)
        ORDER BY 
            CASE WHEN d.file_name ILIKE :q_start THEN 0 ELSE 1 END,
            d.created_at DESC
        LIMIT :limit OFFSET :offset
    """)

    COUNT_SQL = text(f"""
        SELECT COUNT(*) FROM documents d
        WHERE d.approved = true AND ({word_filter})
        AND (:subject IS NULL OR d.subject ILIKE '%' || :subject || '%')
        AND (:class_name IS NULL OR d.class_name ILIKE '%' || :class_name || '%')
        AND (:year IS NULL OR d.year = :year)
    """)

    cache_key = f"search:{query}:{page}:{subject}:{class_name}:{year}"
    
    cache = await get_cache()
    cached = await cache.get(cache_key)
    if cached:
        return [SearchRow(**r) for r in cached["rows"]], cached["total"]

    try:
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
