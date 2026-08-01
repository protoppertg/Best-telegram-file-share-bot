"""Force align database schema with models.

Revision ID: 007
Revises: 006
Create Date: 2024-01-07 00:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Safely add any missing columns to users
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_id BIGINT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(255)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(255)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name VARCHAR(255)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT false")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_expiry TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT false")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS search_count INTEGER DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS upload_count INTEGER DEFAULT 0")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_reset_date DATE DEFAULT CURRENT_DATE")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()")

    # Safely add any missing columns to documents
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_id TEXT")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS message_id BIGINT")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_name TEXT")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS subject VARCHAR(255)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS category VARCHAR(100)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS class_name VARCHAR(100)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS year INTEGER")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS keywords TEXT[]")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_by BIGINT")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS approved BOOLEAN DEFAULT true")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()")

    # Safely add any missing columns to bot_settings
    op.execute("ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS key VARCHAR(50)")
    op.execute("ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS value TEXT")

def downgrade() -> None:
    pass
