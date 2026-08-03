"""add rss_seen_items table

Revision ID: d1e2f3a4b5c6
Revises: c8d9e0f1a2b3
Create Date: 2026-08-02 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rss_seen_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("pipeline_run_id", sa.Integer(), nullable=True),
        sa.Column("feed_url", sa.String(length=1024), nullable=False),
        sa.Column("item_guid", sa.String(length=1024), nullable=False),
        sa.Column("item_url", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"]),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "item_guid", name="uq_rss_seen_channel_guid"),
    )
    op.create_index("ix_rss_seen_items_channel_id", "rss_seen_items", ["channel_id"])
    op.create_index(
        "ix_rss_seen_items_channel_url",
        "rss_seen_items",
        ["channel_id", "item_url"],
    )


def downgrade() -> None:
    op.drop_index("ix_rss_seen_items_channel_url", table_name="rss_seen_items")
    op.drop_index("ix_rss_seen_items_channel_id", table_name="rss_seen_items")
    op.drop_table("rss_seen_items")
