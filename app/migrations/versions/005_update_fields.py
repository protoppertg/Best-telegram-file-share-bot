"""Add class_name, remove university and semester.

Revision ID: 005
Revises: 004
Create Date: 2024-01-05 00:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('documents', sa.Column('class_name', sa.String(length=100), nullable=True))
    op.create_index('ix_documents_class_name', 'documents', ['class_name'])
    op.drop_column('documents', 'university')
    op.drop_column('documents', 'semester')

def downgrade() -> None:
    op.add_column('documents', sa.Column('university', sa.String(length=255), nullable=True))
    op.add_column('documents', sa.Column('semester', sa.String(length=50), nullable=True))
    op.drop_index('ix_documents_class_name', table_name='documents')
    op.drop_column('documents', 'class_name')
