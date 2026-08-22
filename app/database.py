"""Async SQLAlchemy engine, session factory, and Base model."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

class Base(DeclarativeBase):
    pass

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL, echo=False, pool_size=5, max_overflow=10, pool_pre_ping=True,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession,
)

@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

async def init_db():
    """Creates tables, extensions, and safely adds missing columns."""
    async with engine.begin() as conn:
        # 1. Create PostgreSQL extensions for smart dedupe
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        
        # 2. Create tables
        await conn.run_sync(Base.metadata.create_all)
        
        # 3. Safely add missing columns
        alters = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT false",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS aura INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS perm_search_bonus INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_role VARCHAR(50)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS study_buddy_subject VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS chat_partner_id BIGINT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_name VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_language VARCHAR(50)",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_code VARCHAR(15) UNIQUE",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS class_name VARCHAR(100)",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS keywords TEXT[]",
            "ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS value TEXT",
            "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS password VARCHAR(255)"
        ]
        for sql in alters:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass
            
        # 4. Backfill missing doc_codes sequentially for existing rows
        await conn.execute(text("""
            UPDATE documents 
            SET doc_code = 'DOC-' || lpad(id::text, 5, '0') 
            WHERE doc_code IS NULL OR doc_code = ''
        """))
