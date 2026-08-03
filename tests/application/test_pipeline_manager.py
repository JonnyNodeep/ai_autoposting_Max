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
    repo.stop_all_active_by_channel = AsyncMock(return_value=[1])
    repo.list_active_by_channel = AsyncMock(return_value=[created])
    repo.update = AsyncMock()
    repo.create = AsyncMock(return_value=created)

    with patch("app.application.pipeline.manage_pipeline.scheduler_service") as sched:
        sched.add_pipeline_job = MagicMock()
        sched.remove_pipeline_job = MagicMock()
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
    repo.stop_all_active_by_channel.assert_awaited_once_with(5)
    sched.remove_pipeline_job.assert_called_once_with(1)
    sched.add_pipeline_job.assert_called_once_with(2, ["12:00"], "https://max.ru/c")
    sched.add_rss_poll_job.assert_not_called()


@pytest.mark.asyncio
async def test_stop_by_channel():
    repo = AsyncMock()
    repo.stop_all_active_by_channel = AsyncMock(return_value=[7])

    with patch("app.application.pipeline.manage_pipeline.scheduler_service") as sched:
        sched.remove_pipeline_job = MagicMock()
        mgr = PipelineManager(repo)
        await mgr.stop_by_channel(5)

    repo.stop_all_active_by_channel.assert_awaited_once_with(5)
    sched.remove_pipeline_job.assert_called_once_with(7)


@pytest.mark.asyncio
async def test_update_active_config_refreshes_jobs():
    repo = AsyncMock()
    existing = PipelineRun(
        id=11,
        user_id=1,
        max_user_id=10,
        channel_id=1,
        channel_link="https://max.ru/pp",
        blocks_config={},
        frequency="2x_day",
        times=["05:00", "14:00"],
        status=PipelineStatus.ACTIVE,
    )
    repo.get_active_by_channel = AsyncMock(return_value=existing)
    repo.update = AsyncMock()

    blocks = {
        "schedule": {"enabled": True, "frequency": "3x_day", "times": ["05:00", "09:00", "15:00"]},
        "post_gen": {"enabled": True, "mode": "ai", "user_input": "brief"},
    }

    with patch("app.application.pipeline.manage_pipeline.scheduler_service") as sched:
        sched.remove_pipeline_job = MagicMock()
        sched.add_pipeline_job = MagicMock()
        mgr = PipelineManager(repo)
        run = await mgr.update_active_config(1, blocks)

    assert run is existing
    assert run.frequency == "3x_day"
    assert run.times == ["05:00", "09:00", "15:00"]
    assert run.blocks_config["version"] == 2
    repo.update.assert_awaited()
    sched.remove_pipeline_job.assert_called_once_with(11)
    sched.add_pipeline_job.assert_called_once_with(11, ["05:00", "09:00", "15:00"], "https://max.ru/pp")


@pytest.mark.asyncio
async def test_update_active_config_stops_when_schedule_empty():
    repo = AsyncMock()
    existing = PipelineRun(
        id=11,
        user_id=1,
        max_user_id=10,
        channel_id=1,
        channel_link="",
        blocks_config={},
        frequency="daily",
        times=["09:00"],
        status=PipelineStatus.ACTIVE,
    )
    repo.get_active_by_channel = AsyncMock(return_value=existing)
    repo.get_by_id = AsyncMock(return_value=existing)
    repo.update = AsyncMock()

    with patch("app.application.pipeline.manage_pipeline.scheduler_service") as sched:
        sched.remove_pipeline_job = MagicMock()
        mgr = PipelineManager(repo)
        result = await mgr.update_active_config(
            1,
            {"schedule": {"enabled": True, "frequency": "daily", "times": []}},
        )

    assert result is None
    assert existing.status == PipelineStatus.STOPPED
    sched.remove_pipeline_job.assert_called_once_with(11)


@pytest.mark.asyncio
async def test_update_active_config_stops_when_schedule_disabled():
    repo = AsyncMock()
    existing = PipelineRun(
        id=11,
        user_id=1,
        max_user_id=10,
        channel_id=1,
        channel_link="",
        blocks_config={},
        frequency="daily",
        times=["09:00"],
        status=PipelineStatus.ACTIVE,
    )
    repo.get_active_by_channel = AsyncMock(return_value=existing)
    repo.get_by_id = AsyncMock(return_value=existing)
    repo.update = AsyncMock()

    with patch("app.application.pipeline.manage_pipeline.scheduler_service") as sched:
        sched.remove_pipeline_job = MagicMock()
        mgr = PipelineManager(repo)
        result = await mgr.update_active_config(
            1,
            {"schedule": {"enabled": False, "frequency": "daily", "times": ["09:00"]}},
        )

    assert result is None
    assert existing.status == PipelineStatus.STOPPED
    sched.remove_pipeline_job.assert_called_once_with(11)


@pytest.mark.asyncio
async def test_update_active_config_noop_without_active_run():
    repo = AsyncMock()
    repo.get_active_by_channel = AsyncMock(return_value=None)

    with patch("app.application.pipeline.manage_pipeline.scheduler_service") as sched:
        mgr = PipelineManager(repo)
        result = await mgr.update_active_config(
            1,
            {"schedule": {"enabled": True, "frequency": "daily", "times": ["09:00"]}},
        )

    assert result is None
    repo.update.assert_not_called()
    sched.add_pipeline_job.assert_not_called()
    sched.remove_pipeline_job.assert_not_called()


@pytest.mark.asyncio
async def test_start_rss_mode_uses_interval_job():
    repo = AsyncMock()
    repo.stop_all_active_by_channel = AsyncMock(return_value=[])
    created = PipelineRun(
        id=9,
        user_id=1,
        max_user_id=10,
        channel_id=5,
        channel_link="https://max.ru/c",
        blocks_config={},
        frequency="daily",
        times=[],
        status=PipelineStatus.ACTIVE,
    )
    repo.list_active_by_channel = AsyncMock(return_value=[created])
    repo.create = AsyncMock(return_value=created)
    rss_repo = AsyncMock()

    blocks = {
        "news_rss": {
            "enabled": True,
            "feeds": ["https://example.com/rss"],
            "mode": "on_new",
            "poll_interval_minutes": 5,
            "include_keywords": ["bitcoin"],
            "exclude_keywords": ["giveaway"],
            "niche": "crypto",
        },
        "schedule": {"enabled": False, "frequency": "daily", "times": []},
        "post_gen": {"enabled": True, "mode": "ai", "user_input": ""},
    }

    with patch("app.application.pipeline.manage_pipeline.scheduler_service") as sched, patch(
        "app.application.pipeline.manage_pipeline.baseline_mark",
        new_callable=AsyncMock,
    ) as baseline:
        sched.add_rss_poll_job = MagicMock()
        sched.add_pipeline_job = MagicMock()
        mgr = PipelineManager(repo, rss_repo)
        run = await mgr.start(
            user_id=1,
            max_user_id=10,
            channel_id=5,
            channel_link="https://max.ru/c",
            blocks_config=blocks,
            frequency="daily",
            times=[],
        )

    assert run.id == 9
    baseline.assert_awaited()
    sched.add_rss_poll_job.assert_called_once_with(9, 5)
    sched.add_pipeline_job.assert_not_called()
