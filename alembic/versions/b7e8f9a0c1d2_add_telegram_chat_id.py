"""add telegram_chat_id to channels

Revision ID: b7e8f9a0c1d2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-01 16:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e8f9a0c1d2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "channels",
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("channels", "telegram_chat_id")
