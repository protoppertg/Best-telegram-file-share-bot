"""Async SQLAlchemy engine, session factory, and Base model."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
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


async def force_schema_sync():
    """Forces database to create tables and columns if they don't exist."""
    async with engine.begin() as conn:
        # 1. Create Tables if they don't exist
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                is_premium BOOLEAN DEFAULT false,
                premium_expiry TIMESTAMP WITH TIME ZONE,
                is_banned BOOLEAN DEFAULT false,
                search_count INTEGER DEFAULT 0,
                upload_count INTEGER DEFAULT 0,
                last_reset_date DATE DEFAULT CURRENT_DATE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS documents (
                id SERIAL PRIMARY KEY,
                file_id TEXT NOT NULL,
                message_id BIGINT,
                file_name TEXT NOT NULL,
                subject VARCHAR(255),
                category VARCHAR(100),
                class_name VARCHAR(100),
                year INTEGER,
                keywords TEXT[],
                description TEXT,
                uploaded_by BIGINT,
                approved BOOLEAN DEFAULT true,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key VARCHAR(50) PRIMARY KEY,
                value TEXT
            );
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS search_logs (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                query TEXT,
                result_count INTEGER DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """))
        
        # 2. Force add missing columns (in case table existed but was missing columns)
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_id BIGINT"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(255)"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(255)"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name VARCHAR(255)"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT false"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_expiry TIMESTAMP WITH TIME ZONE"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT false"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS search_count INTEGER DEFAULT 0"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS upload_count INTEGER DEFAULT 0"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_reset_date DATE DEFAULT CURRENT_DATE"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"))

        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_id TEXT"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS message_id BIGINT"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_name TEXT"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS subject VARCHAR(255)"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS category VARCHAR(100)"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS class_name VARCHAR(100)"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS year INTEGER"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS keywords TEXT[]"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS description TEXT"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_by BIGINT"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS approved BOOLEAN DEFAULT true"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"))
        await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"))

        await conn.execute(text("ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS key VARCHAR(50)"))
        await conn.execute(text("ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS value TEXT"))
        
        await conn.execute(text("ALTER TABLE search_logs ADD COLUMN IF NOT EXISTS user_id BIGINT"))
        await conn.execute(text("ALTER TABLE search_logs ADD COLUMN IF NOT EXISTS query TEXT"))
        await conn.execute(text("ALTER TABLE search_logs ADD COLUMN IF NOT EXISTS result_count INTEGER DEFAULT 0"))
        await conn.execute(text("ALTER TABLE search_logs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"))
