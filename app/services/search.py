"""PostgreSQL full-text + trigram search service."""

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
    university: Optional[str]
    semester: Optional[str]
    year: Optional[int]

SEARCH_SQL = text("""
    SELECT d.id, d.file_name, d.subject, d.category, d.university, d.semester, d.year,
           COALESCE(ts_rank(d.search_vector, websearch_to_tsquery('english', :q)), 0.0) AS fts_rank,
           GREATEST(similarity(d.file_name, :q), similarity(coalesce(d.subject, ''), :q), similarity(coalesce(d.category, ''), :q)) AS trgm_rank,
           CASE WHEN d.file_name ILIKE '%' || :q || '%' THEN 0.5 ELSE 0.0 END AS ilike_rank
    FROM documents d
    WHERE d.approved = true AND (
        d.search_vector @@ websearch_to_tsquery('english', :q) OR similarity(d.file_name, :q) > 0.1 OR
        similarity(coalesce(d.subject, ''), :q) > 0.1 OR similarity(coalesce(d.category, ''), :q) > 0.1 OR
        d.file_name ILIKE '%' || :q || '%' OR coalesce(d.subject, '') ILIKE '%' || :q || '%')
    ORDER BY (fts_rank * 1.0 + trgm_rank * 0.5 + ilike_rank) DESC, d.created_at DESC
    LIMIT :limit OFFSET :offset
""")

COUNT_SQL = text("""
    SELECT COUNT(*) FROM documents d
    WHERE d.approved = true AND (
        d.search_vector @@ websearch_to_tsquery('english', :q) OR similarity(d.file_name, :q) > 0.1 OR
        similarity(coalesce(d.subject, ''), :q) > 0.1 OR similarity(coalesce(d.category, ''), :q) > 0.1 OR
        d.file_name ILIKE '%' || :q || '%' OR coalesce(d.subject, '') ILIKE '%' || :q || '%')
""")

async def search_documents(session: AsyncSession, query: str, page: int = 1, per_page: Optional[int] = None, *, subject: Optional[str] = None, category: Optional[str] = None, university: Optional[str] = None, semester: Optional[str] = None, year: Optional[int] = None) -> tuple[List[SearchRow], int]:
    per_page = per_page or settings.SEARCH_RESULTS_PER_PAGE
    offset = (page - 1) * per_page
    normalized = query.strip()
    cache_key = f"search:{normalized}:{page}"
    
    cache = await get_cache()
    cached = await cache.get(cache_key)
    if cached:
        return [SearchRow(**r) for r in cached["rows"]], cached["total"]

    result = await session.execute(SEARCH_SQL, {"q": normalized, "limit": per_page, "offset": offset})
    raw_rows = result.fetchall()
    count_result = await session.execute(COUNT_SQL, {"q": normalized})
    total = count_result.scalar() or 0

    rows = [{"id": r.id, "file_name": r.file_name, "subject": r.subject, "category": r.category, "university": r.university, "semester": r.semester, "year": r.year} for r in raw_rows]
    await cache.set(cache_key, {"rows": rows, "total": total}, ttl=settings.CACHE_TTL_SECONDS)
    return [SearchRow(**r) for r in rows], total
