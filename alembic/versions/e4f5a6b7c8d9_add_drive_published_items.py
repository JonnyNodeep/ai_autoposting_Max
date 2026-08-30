"""add drive_published_items and feature_drive_whitelist setting

Revision ID: e4f5a6b7c8d9
Revises: c2d3e4f5a6b7
Create Date: 2026-08-29 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drive_published_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("pipeline_run_id", sa.Integer(), nullable=True),
        sa.Column("drive_file_id", sa.String(length=128), nullable=False),
        sa.Column("file_name", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"]),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_id", "drive_file_id", name="uq_drive_published_channel_file"
        ),
    )
    op.create_index(
        "ix_drive_published_items_channel_id",
        "drive_published_items",
        ["channel_id"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO app_settings (key, value)
            VALUES ('feature_drive_whitelist', to_jsonb(''::text))
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM app_settings WHERE key = 'feature_drive_whitelist'")
    )
    op.drop_index("ix_drive_published_items_channel_id", table_name="drive_published_items")
    op.drop_table("drive_published_items")
