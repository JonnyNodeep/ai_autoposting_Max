from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.pipeline.manage_pipeline import PipelineManager
from app.domain.entities.pipeline_run import PipelineRun, PipelineStatus


def test_calc_next_run_picks_later_today():
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    nxt = PipelineManager._calc_next_run(["09:00", "15:00"], now)
    assert nxt == datetime(2026, 7, 28, 15, 0, tzinfo=UTC)


def test_calc_next_run_rolls_to_tomorrow():
    now = datetime(2026, 7, 28, 20, 0, tzinfo=UTC)
    nxt = PipelineManager._calc_next_run(["09:00", "15:00"], now)
    assert nxt == datetime(2026, 7, 29, 9, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_start_stops_existing_active():
    repo = AsyncMock()
    existing = PipelineRun(
        id=1,
        user_id=1,
        max_user_id=10,
        channel_id=5,
        channel_link="https://max.ru/c",
        blocks_config={},
        frequency="daily",
        times=["12:00"],
        status=PipelineStatus.ACTIVE,
    )
    created = PipelineRun(
        id=2,
        user_id=1,
        max_user_id=10,
        channel_id=5,
        channel_link="https://max.ru/c",
        blocks_config={"a": 1},
        frequency="daily",
        times=["12:00"],
        status=PipelineStatus.ACTIVE,
    )
    repo.get_active_by_channel = AsyncMock(return_value=existing)
    repo.update = AsyncMock()
    repo.create = AsyncMock(return_value=created)

    with patch("app.application.pipeline.manage_pipeline.scheduler_service") as sched:
        sched.add_pipeline_job = MagicMock()
        mgr = PipelineManager(repo)
        run = await mgr.start(
            user_id=1,
            max_user_id=10,
            channel_id=5,
            channel_link="https://max.ru/c",
            blocks_config={"a": 1},
            frequency="daily",
            times=["12:00"],
        )

    assert run.id == 2
    assert existing.status == PipelineStatus.STOPPED
    repo.update.assert_awaited()
    sched.add_pipeline_job.assert_called_once_with(2, ["12:00"], "https://max.ru/c")


@pytest.mark.asyncio
async def test_stop_by_channel():
    repo = AsyncMock()
    existing = PipelineRun(
        id=7,
        user_id=1,
        max_user_id=10,
        channel_id=5,
        channel_link="",
        blocks_config={},
        frequency="daily",
        times=["12:00"],
        status=PipelineStatus.ACTIVE,
    )
    repo.get_active_by_channel = AsyncMock(return_value=existing)
    repo.get_by_id = AsyncMock(return_value=existing)
    repo.update = AsyncMock()

    with patch("app.application.pipeline.manage_pipeline.scheduler_service") as sched:
        sched.remove_pipeline_job = MagicMock()
        mgr = PipelineManager(repo)
        await mgr.stop_by_channel(5)

    assert existing.status == PipelineStatus.STOPPED
    sched.remove_pipeline_job.assert_called_once_with(7)
