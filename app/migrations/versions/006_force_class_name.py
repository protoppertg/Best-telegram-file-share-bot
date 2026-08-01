"""Force add class_name column if missing.

Revision ID: 006
Revises: 005
Create Date: 2024-01-06 00:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # This safely adds the column if it doesn't exist yet
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS class_name VARCHAR(100)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_class_name ON documents (class_name)")

def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS class_name")
