"""add telegram_link to channels

Revision ID: c8d9e0f1a2b3
Revises: b7e8f9a0c1d2
Create Date: 2026-08-01 18:50:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b7e8f9a0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "channels",
        sa.Column("telegram_link", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("channels", "telegram_link")
