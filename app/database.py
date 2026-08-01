"""Async SQLAlchemy engine, session factory, and Base model."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)
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
    """Creates tables and safely adds missing columns without Alembic."""
    async with engine.begin() as conn:
        # 1. Create all tables natively
        await conn.run_sync(Base.metadata.create_all)
        
        # 2. Safely add columns that were introduced in later updates
        alters = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT false",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS class_name VARCHAR(100)",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS keywords TEXT[]",
            "ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS value TEXT"
        ]
        for sql in alters:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass # Ignore if it already exists
