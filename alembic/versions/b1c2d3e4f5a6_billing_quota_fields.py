"""billing quota and payment kind fields

Revision ID: b1c2d3e4f5a6
Revises: a9b8c7d6e5f4
Create Date: 2026-08-11 16:40:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("posts_per_day", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "subscriptions",
        sa.Column("generations_quota", sa.Integer(), nullable=False, server_default="30"),
    )
    op.add_column(
        "subscriptions",
        sa.Column("generations_used", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "expiry_notified_3d",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "expiry_notified_1d",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "expiry_notified_0d",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Creator was 3 channels; bump existing creator rows to 5.
    op.execute(
        "UPDATE subscriptions SET channels_limit = 5 "
        "WHERE tier = 'creator' AND channels_limit = 3"
    )

    op.add_column(
        "payments",
        sa.Column("posts_per_day", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "payments",
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="new"),
    )


def downgrade() -> None:
    op.drop_column("payments", "kind")
    op.drop_column("payments", "posts_per_day")
    op.drop_column("subscriptions", "expiry_notified_0d")
    op.drop_column("subscriptions", "expiry_notified_1d")
    op.drop_column("subscriptions", "expiry_notified_3d")
    op.drop_column("subscriptions", "generations_used")
    op.drop_column("subscriptions", "generations_quota")
    op.drop_column("subscriptions", "posts_per_day")
