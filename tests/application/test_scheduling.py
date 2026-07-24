import pytest
from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock

from app.application.scheduling.manage_schedule import SchedulePostUseCase, ConfirmPublishUseCase
from app.domain.entities.channel import Channel
from app.domain.entities.publish_schedule import PublishSchedule, ScheduleStatus
from app.domain.entities.content_post import ContentPost, PostStatus


@pytest.mark.asyncio
async def test_schedule_post():
    mock_schedule_repo = AsyncMock()
    expected = PublishSchedule(
        id=1, post_id=5, channel_id=10,
        scheduled_at=datetime.now(UTC),
    )
    mock_schedule_repo.create.return_value = expected

    mock_post_repo = AsyncMock()
    mock_post_repo.get_by_id.return_value = ContentPost(id=5, topic_id=1, title="Test", text="T", status=PostStatus.READY)

    uc = SchedulePostUseCase(mock_schedule_repo, mock_post_repo)
    sched = await uc.execute(5, 10, datetime.now(UTC))

    assert sched.post_id == 5
    assert sched.channel_id == 10
    assert mock_schedule_repo.create.called


@pytest.mark.asyncio
async def test_schedule_post_not_found():
    mock_schedule_repo = AsyncMock()
    mock_post_repo = AsyncMock()
    mock_post_repo.get_by_id.return_value = None

    uc = SchedulePostUseCase(mock_schedule_repo, mock_post_repo)

    with pytest.raises(ValueError, match="Post 999 not found"):
        await uc.execute(999, 10, datetime.now(UTC))


@pytest.mark.asyncio
async def test_confirm_publish():
    mock_schedule_repo = AsyncMock()
    mock_schedule_repo.get_by_id.return_value = PublishSchedule(
        id=1, post_id=5, channel_id=100,
        scheduled_at=datetime.now(UTC),
    )

    mock_post_repo = AsyncMock()
    mock_post_repo.get_by_id.return_value = ContentPost(
        id=5, topic_id=1, title="Test", text="Content", cta="Go!",
        image_url=None, status=PostStatus.READY,
    )

    mock_max = AsyncMock()
    mock_max.send_message.return_value = {"ok": True}

    mock_channel_repo = AsyncMock()
    mock_channel_repo.get_by_id.return_value = Channel(
        id=100,
        owner_id=1,
        max_chat_id=999001,
        title="Test channel",
    )

    uc = ConfirmPublishUseCase(mock_schedule_repo, mock_post_repo, mock_channel_repo, mock_max)
    result = await uc.execute(1)

    assert result is True
    assert mock_max.send_message.called
    assert mock_post_repo.update.called
