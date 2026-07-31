"""Document CRUD operations."""

from __future__ import annotations

from typing import Any, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document
from app.utils.logger import logger


async def create_document(session: AsyncSession, *, file_id: str, message_id: Optional[int] = None, file_name: str, subject: Optional[str] = None, category: Optional[str] = None, university: Optional[str] = None, semester: Optional[str] = None, year: Optional[int] = None, keywords: Optional[List[str]] = None, description: Optional[str] = None, uploaded_by: Optional[int] = None, approved: bool = True) -> Document:
    doc = Document(file_id=file_id, message_id=message_id, file_name=file_name, subject=subject, category=category, university=university, semester=semester, year=year, keywords=keywords or [], description=description, uploaded_by=uploaded_by, approved=approved)
    session.add(doc)
    await session.flush()
    return doc

async def get_document_by_id(session: AsyncSession, doc_id: int) -> Optional[Document]:
    result = await session.execute(select(Document).where(Document.id == doc_id))
    return result.scalar_one_or_none()

async def update_document(session: AsyncSession, doc_id: int, **kwargs: Any) -> Optional[Document]:
    doc = await get_document_by_id(session, doc_id)
    if not doc: return None
    for key, value in kwargs.items():
        if hasattr(doc, key) and value is not None: setattr(doc, key, value)
    await session.flush()
    return doc

async def delete_document(session: AsyncSession, doc_id: int) -> bool:
    doc = await get_document_by_id(session, doc_id)
    if not doc: return False
    await session.delete(doc)
    await session.flush()
    return True

async def count_documents(session: AsyncSession, approved_only: bool = False) -> int:
    stmt = select(func.count(Document.id))
    if approved_only: stmt = stmt.where(Document.approved == True)
    return (await session.execute(stmt)).scalar() or 0

async def list_documents(session: AsyncSession, page: int = 1, per_page: int = 10, approved_only: bool = False) -> tuple[List[Document], int]:
    stmt = select(Document).order_by(Document.created_at.desc())
    if approved_only: stmt = stmt.where(Document.approved == True)
    count_stmt = select(func.count()).select_from(Document)
    if approved_only: count_stmt = count_stmt.where(Document.approved == True)
    total = (await session.execute(count_stmt)).scalar() or 0
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await session.execute(stmt)
    return result.scalars().all(), total

async def get_pending_documents(session: AsyncSession, page: int = 1, per_page: int = 10) -> tuple[List[Document], int]:
    stmt = select(Document).where(Document.approved == False).order_by(Document.created_at.desc())
    count_stmt = select(func.count(Document.id)).where(Document.approved == False)
    total = (await session.execute(count_stmt)).scalar() or 0
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await session.execute(stmt)
    return result.scalars().all(), total

async def approve_document(session: AsyncSession, doc_id: int) -> bool:
    doc = await get_document_by_id(session, doc_id)
    if not doc: return False
    doc.approved = True
    await session.flush()
    return True
