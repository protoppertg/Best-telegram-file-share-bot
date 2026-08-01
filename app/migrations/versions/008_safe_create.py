"""Safe create tables if they don't exist.

Revision ID: 008
Revises: 007
Create Date: 2024-01-08 00:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Create tables ONLY if they don't exist. This prevents all missing table errors.
    op.execute("""
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
    
    CREATE TABLE IF NOT EXISTS bot_settings (
        key VARCHAR(50) PRIMARY KEY,
        value TEXT
    );
    
    CREATE TABLE IF NOT EXISTS search_logs (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        query TEXT,
        result_count INTEGER DEFAULT 0,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)

def downgrade() -> None:
    pass
