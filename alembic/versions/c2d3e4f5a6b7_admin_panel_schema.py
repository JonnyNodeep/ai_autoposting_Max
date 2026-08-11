"""admin panel schema: settings, discounts, waitlist, broadcasts, referrals

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-11 17:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.add_column(
        "users",
        sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column("referral_code", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("referred_by_user_id", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint("uq_users_referral_code", "users", ["referral_code"])
    op.create_foreign_key(
        "fk_users_referred_by",
        "users",
        "users",
        ["referred_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "payments",
        sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "payments",
        sa.Column(
            "amount_before_discount",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    op.create_table(
        "waitlist_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("max_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admitted_user_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_waitlist_entries_status", "waitlist_entries", ["status"])

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_admin_audit_log_created_at", "admin_audit_log", ["created_at"])

    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("segment", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("created_by", sa.String(length=128), nullable=False, server_default="admin"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "broadcast_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "broadcast_id",
            sa.Integer(),
            sa.ForeignKey("broadcasts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("max_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_broadcast_deliveries_broadcast_id",
        "broadcast_deliveries",
        ["broadcast_id"],
    )

    op.create_table(
        "referral_rewards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("referrer_user_id", sa.Integer(), nullable=False),
        sa.Column("referred_user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="signup"),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Seed default settings (avoid % in strings — SQLAlchemy bind interpolation)
    op.execute(
        sa.text(
            """
            INSERT INTO app_settings (key, value) VALUES
            ('max_users', to_jsonb(10)),
            ('billing_prices', '{
              "base": {"solo": 490, "creator": 1990, "studio": 3490},
              "per_post": {"solo": 12, "creator": 11, "studio": 10},
              "channels": {"solo": 1, "creator": 5, "studio": 10},
              "posts_per_day_options": [1, 2, 3, 5]
            }'::json),
            ('feature_rss_whitelist', to_jsonb(''::text)),
            ('feature_video_whitelist', to_jsonb(''::text)),
            ('feature_audio_whitelist', to_jsonb(''::text))
            """
        )
    )


def downgrade() -> None:
    op.drop_table("referral_rewards")
    op.drop_table("broadcast_deliveries")
    op.drop_table("broadcasts")
    op.drop_index("ix_admin_audit_log_created_at", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
    op.drop_index("ix_waitlist_entries_status", table_name="waitlist_entries")
    op.drop_table("waitlist_entries")
    op.drop_column("payments", "amount_before_discount")
    op.drop_column("payments", "discount_percent")
    op.drop_constraint("fk_users_referred_by", "users", type_="foreignkey")
    op.drop_constraint("uq_users_referral_code", "users", type_="unique")
    op.drop_column("users", "referred_by_user_id")
    op.drop_column("users", "referral_code")
    op.drop_column("users", "discount_percent")
    op.drop_table("app_settings")
