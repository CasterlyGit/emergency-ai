"""Initial schema: api_keys, request_logs, statute_chunks.

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("owner_email", sa.String(length=256), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("requests_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    op.create_table(
        "request_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("city_slug", sa.String(length=64), nullable=False),
        sa.Column("urgency", sa.String(length=32), nullable=False),
        sa.Column("ttft_ms", sa.Integer(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_request_logs_api_key_id", "request_logs", ["api_key_id"])

    op.create_table(
        "statute_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("jurisdiction", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # vector(384) from pgvector — raw SQL because SQLAlchemy type not in core alembic
        sa.Column("embedding", sa.Text(), nullable=True),  # placeholder; see raw below
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Recreate embedding column as proper vector(384)
    op.execute("ALTER TABLE statute_chunks DROP COLUMN embedding")
    op.execute("ALTER TABLE statute_chunks ADD COLUMN embedding vector(384)")
    op.create_index("ix_statute_chunks_jurisdiction", "statute_chunks", ["jurisdiction"])


def downgrade() -> None:
    op.drop_table("statute_chunks")
    op.drop_table("request_logs")
    op.drop_table("api_keys")
    op.execute("DROP EXTENSION IF EXISTS vector")
