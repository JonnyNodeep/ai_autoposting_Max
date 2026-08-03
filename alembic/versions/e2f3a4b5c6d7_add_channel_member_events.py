"""add channel_member_events table

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-02 13:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_member_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("max_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("max_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_channel_member_events_channel_id",
        "channel_member_events",
        ["channel_id"],
    )
    op.create_index(
        "ix_channel_member_events_max_chat_id",
        "channel_member_events",
        ["max_chat_id"],
    )
    op.create_index(
        "ix_channel_member_events_event_type",
        "channel_member_events",
        ["event_type"],
    )
    op.create_index(
        "ix_channel_member_events_created_at",
        "channel_member_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_channel_member_events_created_at", table_name="channel_member_events")
    op.drop_index("ix_channel_member_events_event_type", table_name="channel_member_events")
    op.drop_index("ix_channel_member_events_max_chat_id", table_name="channel_member_events")
    op.drop_index("ix_channel_member_events_channel_id", table_name="channel_member_events")
    op.drop_table("channel_member_events")
