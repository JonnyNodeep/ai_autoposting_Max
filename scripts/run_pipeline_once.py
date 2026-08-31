#!/usr/bin/env python3
"""Run a single pipeline step manually (catch-up / maintenance)."""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from app.infrastructure.scheduler.service import SchedulerService


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run one pipeline step by run_id")
    parser.add_argument("--run-id", type=int, required=True, help="pipeline_runs.id")
    parser.add_argument(
        "--slot-time",
        type=str,
        default="catchup",
        help="Slot label for logs and owner notification (default: catchup)",
    )
    args = parser.parse_args()

    logger.info(
        "Manual pipeline run starting run_id={} slot_time={!r}",
        args.run_id,
        args.slot_time,
    )
    service = SchedulerService()
    await service.run_pipeline_step(args.run_id, slot_time=args.slot_time)
    logger.info("Manual pipeline run finished run_id={}", args.run_id)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
