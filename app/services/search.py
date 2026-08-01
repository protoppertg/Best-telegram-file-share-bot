"""PostgreSQL full-text search service (Dynamic SQL Version)."""

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

    # Dynamically build the WHERE clause and params
    where_clauses = ["d.approved = true"]
    params = {
        "limit": per_page, 
        "offset": offset,
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
    
    # Only add filters to SQL if they are actually provided (prevents None type crashes)
    if subject:
        params["subject"] = f"%{subject}%"
        where_clauses.append("d.subject ILIKE :subject")
    
    if class_name:
        params["class_name"] = f"%{class_name}%"
        where_clauses.append("d.class_name ILIKE :class_name")
        
    if year:
        params["year"] = year
        where_clauses.append("d.year = :year")

    where_clause_str = " AND ".join(where_clauses)

    SEARCH_SQL = text(f"""
        SELECT d.id, d.file_name, d.subject, d.category, d.class_name, d.year
        FROM documents d
        WHERE {where_clause_str}
        ORDER BY 
            CASE WHEN d.file_name ILIKE :q_start THEN 0 ELSE 1 END,
            d.created_at DESC
        LIMIT :limit OFFSET :offset
    """)

    COUNT_SQL = text(f"""
        SELECT COUNT(*) FROM documents d
        WHERE {where_clause_str}
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
