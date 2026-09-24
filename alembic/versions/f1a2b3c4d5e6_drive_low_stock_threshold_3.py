"""drive low_stock_threshold default 5 -> 3

Revision ID: f1a2b3c4d5e6
Revises: e4f5a6b7c8d9
Create Date: 2026-09-03 20:00:00.000000
"""
from __future__ import annotations

import copy
import json
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _patch_threshold(blocks_config: dict[str, Any]) -> dict[str, Any] | None:
    cfg = copy.deepcopy(blocks_config)
    changed = False

    drive = cfg.get("drive_video")
    if isinstance(drive, dict) and drive.get("low_stock_threshold") == 5:
        drive["low_stock_threshold"] = 3
        changed = True

    if cfg.get("version") == 2:
        for step in cfg.get("steps") or []:
            if step.get("type") != "drive_video":
                continue
            step_cfg = step.get("config")
            if isinstance(step_cfg, dict) and step_cfg.get("low_stock_threshold") == 5:
                step_cfg["low_stock_threshold"] = 3
                changed = True

    return cfg if changed else None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, blocks_config FROM pipeline_runs "
            "WHERE blocks_config IS NOT NULL"
        )
    ).fetchall()

    for row in rows:
        blocks_config = row.blocks_config
        if not isinstance(blocks_config, dict):
            continue
        patched = _patch_threshold(blocks_config)
        if patched is None:
            continue
        conn.execute(
            sa.text(
                "UPDATE pipeline_runs SET blocks_config = CAST(:cfg AS JSON) WHERE id = :id"
            ),
            {"cfg": json.dumps(patched), "id": row.id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, blocks_config FROM pipeline_runs "
            "WHERE blocks_config IS NOT NULL"
        )
    ).fetchall()

    for row in rows:
        blocks_config = row.blocks_config
        if not isinstance(blocks_config, dict):
            continue
        cfg = copy.deepcopy(blocks_config)
        changed = False

        drive = cfg.get("drive_video")
        if isinstance(drive, dict) and drive.get("low_stock_threshold") == 3:
            drive["low_stock_threshold"] = 5
            changed = True

        if cfg.get("version") == 2:
            for step in cfg.get("steps") or []:
                if step.get("type") != "drive_video":
                    continue
                step_cfg = step.get("config")
                if isinstance(step_cfg, dict) and step_cfg.get("low_stock_threshold") == 3:
                    step_cfg["low_stock_threshold"] = 5
                    changed = True

        if not changed:
            continue
        conn.execute(
            sa.text(
                "UPDATE pipeline_runs SET blocks_config = CAST(:cfg AS JSON) WHERE id = :id"
            ),
            {"cfg": json.dumps(cfg), "id": row.id},
        )
