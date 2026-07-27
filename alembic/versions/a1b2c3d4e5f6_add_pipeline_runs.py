"""add_pipeline_runs

Revision ID: a1b2c3d4e5f6
Revises: 9d7cd982aa43
Create Date: 2026-07-27 17:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9d7cd982aa43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pipeline_runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('channel_id', sa.Integer(), sa.ForeignKey('channels.id'), nullable=False),
        sa.Column('channel_link', sa.String(512), nullable=False, server_default=''),
        sa.Column('blocks_config', sa.JSON(), nullable=True),
        sa.Column('frequency', sa.String(32), nullable=False, server_default='daily'),
        sa.Column('times', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='active'),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pipeline_runs_user_id'), 'pipeline_runs', ['user_id'])
    op.create_index(op.f('ix_pipeline_runs_channel_id'), 'pipeline_runs', ['channel_id'])
    op.create_index(op.f('ix_pipeline_runs_status'), 'pipeline_runs', ['status'])


def downgrade() -> None:
    op.drop_index(op.f('ix_pipeline_runs_status'), table_name='pipeline_runs')
    op.drop_index(op.f('ix_pipeline_runs_channel_id'), table_name='pipeline_runs')
    op.drop_index(op.f('ix_pipeline_runs_user_id'), table_name='pipeline_runs')
    op.drop_table('pipeline_runs')
