"""unique one active pipeline run per channel

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-02 17:35:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep newest active run per channel; stop older duplicates.
    op.execute(
        """
        UPDATE pipeline_runs AS older
        SET status = 'stopped'
        FROM pipeline_runs AS newer
        WHERE older.channel_id = newer.channel_id
          AND older.status = 'active'
          AND newer.status = 'active'
          AND older.id < newer.id
        """
    )
    op.create_index(
        "uq_pipeline_runs_one_active_per_channel",
        "pipeline_runs",
        ["channel_id"],
        unique=True,
        postgresql_where="status = 'active'",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_pipeline_runs_one_active_per_channel",
        table_name="pipeline_runs",
    )
