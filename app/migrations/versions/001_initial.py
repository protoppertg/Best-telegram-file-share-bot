"""Initial migration: create tables, extensions, indexes.

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("first_name", sa.String(255), nullable=True),
        sa.Column("last_name", sa.String(255), nullable=True),
        sa.Column("is_premium", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("premium_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("search_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("upload_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_reset_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("university", sa.String(255), nullable=True),
        sa.Column("semester", sa.String(50), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("keywords", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("uploaded_by", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.execute("ALTER TABLE documents ADD COLUMN search_vector tsvector")
    
    op.execute("""
        CREATE OR REPLACE FUNCTION documents_search_vector_update() RETURNS trigger AS $$         BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', coalesce(NEW.file_name, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.subject, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(NEW.category, '')), 'C') ||
                setweight(to_tsvector('english', coalesce(array_to_string(NEW.keywords, ' '), '')), 'D');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    op.execute("""
        CREATE TRIGGER documents_search_vector_trigger
        BEFORE INSERT OR UPDATE ON documents
        FOR EACH ROW EXECUTE FUNCTION documents_search_vector_update();
    """)

    op.create_index("idx_documents_search_vector", "documents", ["search_vector"], postgresql_using="gin")
    op.create_index("idx_documents_file_name_trgm", "documents", ["file_name"], postgresql_using="gin", postgresql_ops={"file_name": "gin_trgm_ops"})
    op.create_index("idx_documents_keywords", "documents", ["keywords"], postgresql_using="gin")
    op.create_index("ix_documents_subject", "documents", ["subject"])
    op.create_index("ix_documents_category", "documents", ["category"])
    op.create_index("ix_documents_university", "documents", ["university"])
    op.create_index("ix_documents_semester", "documents", ["semester"])
    op.create_index("ix_documents_year", "documents", ["year"])
    op.create_index("ix_documents_approved", "documents", ["approved"])

    op.create_table(
        "admins",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("role", sa.String(50), nullable=False, server_default="admin"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admins_telegram_id", "admins", ["telegram_id"])

    op.create_table(
        "search_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_search_logs_user_id", "search_logs", ["user_id"])
    op.create_index("ix_search_logs_created_at", "search_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("search_logs")
    op.drop_table("admins")
    op.drop_index("ix_documents_approved", table_name="documents")
    op.drop_index("ix_documents_year", table_name="documents")
    op.drop_index("ix_documents_semester", table_name="documents")
    op.drop_index("ix_documents_university", table_name="documents")
    op.drop_index("ix_documents_category", table_name="documents")
    op.drop_index("ix_documents_subject", table_name="documents")
    op.drop_index("idx_documents_keywords", table_name="documents")
    op.drop_index("idx_documents_file_name_trgm", table_name="documents")
    op.drop_index("idx_documents_search_vector", table_name="documents")
    op.execute("DROP TRIGGER IF EXISTS documents_search_vector_trigger ON documents")
    op.execute("DROP FUNCTION IF EXISTS documents_search_vector_update()")
    op.drop_table("documents")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
