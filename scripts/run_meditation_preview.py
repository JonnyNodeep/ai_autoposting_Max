#!/usr/bin/env python3
"""Run one meditation slot to owner DM (preview — not published to channel)."""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from app.application.pipeline.meditation_presets import (
    EVENING_UTC,
    LUNCH_UTC,
    MORNING_UTC,
    slot_msk_label,
)
from app.infrastructure.scheduler.service import SchedulerService

ALL_SLOTS = (MORNING_UTC, LUNCH_UTC, EVENING_UTC)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Meditation slot preview to owner DM (uses real slot topic queue)",
    )
    parser.add_argument("--run-id", type=int, required=True, help="pipeline_runs.id")
    parser.add_argument(
        "--slot-time",
        type=str,
        help=f"Slot UTC time, e.g. {MORNING_UTC}",
    )
    parser.add_argument(
        "--all-slots",
        action="store_true",
        help="Run morning, lunch, evening sequentially",
    )
    args = parser.parse_args()

    slots: list[str] = []
    if args.all_slots:
        slots = list(ALL_SLOTS)
    elif args.slot_time:
        slots = [str(args.slot_time).strip()]
    else:
        parser.error("Provide --slot-time or --all-slots")

    service = SchedulerService()
    for slot_time in slots:
        label = slot_msk_label(slot_time) or slot_time
        logger.info(
            "Meditation preview starting run_id={} slot={} (MSK {})",
            args.run_id,
            slot_time,
            label,
        )
        await service.run_meditation_preview_step(args.run_id, slot_time=slot_time)
        logger.info(
            "Meditation preview finished run_id={} slot={}",
            args.run_id,
            slot_time,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
