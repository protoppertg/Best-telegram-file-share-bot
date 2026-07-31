"""Add search_enabled setting.

Revision ID: 004
Revises: 003
Create Date: 2024-01-04 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("INSERT INTO bot_settings (key, value) VALUES ('search_enabled', 'true') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute("DELETE FROM bot_settings WHERE key='search_enabled'")
