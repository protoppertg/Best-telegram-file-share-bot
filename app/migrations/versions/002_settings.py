"""Add bot settings table for Force Sub.

Revision ID: 002
Revises: 001
Create Date: 2024-01-02 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_settings",
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True)
    )
    op.execute("INSERT INTO bot_settings (key, value) VALUES ('force_sub_channel_id', '')")
    op.execute("INSERT INTO bot_settings (key, value) VALUES ('force_sub_invite_link', '')")


def downgrade() -> None:
    op.drop_table("bot_settings")
