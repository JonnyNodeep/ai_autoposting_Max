from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.pipeline.blocks.drive_video import DriveVideoBlock
from app.application.pipeline.context import PipelineContext
from app.infrastructure.services.google_drive_client import DriveVideo


@pytest.fixture
def channel():
    return SimpleNamespace(id=7, max_chat_id=100, title="Test Channel")


@pytest.fixture
def ctx(channel):
    max_client = AsyncMock()
    return PipelineContext(
        channel=channel,
        channel_link="https://max.ru/test",
        run_id=1,
        max_client=max_client,
        openai_client=None,
        target="channel",
        channel_title="Test Channel",
        meta={"owner_max_user_id": 42},
    )


@pytest.mark.asyncio
async def test_drive_video_empty_folder_skips(ctx):
    block = DriveVideoBlock()
    config = {
        "enabled": True,
        "folder_id": "folder1",
        "fixed_caption": "Caption",
        "low_stock_threshold": 5,
    }

    with patch(
        "app.application.pipeline.blocks.drive_video.drive_allowed",
        return_value=True,
    ), patch(
        "app.application.pipeline.blocks.drive_video.list_videos",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.application.pipeline.blocks.drive_video.async_session_factory"
    ) as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_repo = MagicMock()
        mock_repo.get_published_file_ids = AsyncMock(return_value=set())
        with patch(
            "app.application.pipeline.blocks.drive_video.SQLADrivePublishedRepository",
            return_value=mock_repo,
        ):
            await block.execute(ctx, config)

    assert ctx.meta.get("publish_skipped") == "drive_empty"
    assert not ctx.video_local_path


@pytest.mark.asyncio
async def test_drive_video_downloads_next(ctx, tmp_path):
    block = DriveVideoBlock()
    config = {
        "enabled": True,
        "folder_id": "folder1",
        "fixed_caption": "My caption",
        "low_stock_threshold": 5,
        "low_stock_notified_at_remaining": None,
    }
    videos = [
        DriveVideo(
            file_id="f1",
            name="one.mp4",
            mime_type="video/mp4",
            created_time="2026-01-01T00:00:00Z",
        )
    ]
    async def _fake_download(_file_id, dest_path):
        dest_path.write_bytes(b"fake")
        return dest_path

    with patch(
        "app.application.pipeline.blocks.drive_video.drive_allowed",
        return_value=True,
    ), patch(
        "app.application.pipeline.blocks.drive_video.list_videos",
        new=AsyncMock(return_value=videos),
    ), patch(
        "app.application.pipeline.blocks.drive_video.download_file",
        new=AsyncMock(side_effect=_fake_download),
    ), patch(
        "app.application.pipeline.blocks.drive_video.UPLOAD_DIR",
        tmp_path,
    ), patch(
        "app.application.pipeline.blocks.drive_video.async_session_factory"
    ) as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_repo = MagicMock()
        mock_repo.get_published_file_ids = AsyncMock(return_value=set())
        with patch(
            "app.application.pipeline.blocks.drive_video.SQLADrivePublishedRepository",
            return_value=mock_repo,
        ):
            await block.execute(ctx, config)

    assert ctx.meta.get("drive_file_id") == "f1"
    assert ctx.post_text == "My caption"
    assert Path(ctx.video_local_path).exists()


@pytest.mark.asyncio
async def test_drive_video_low_stock_notify(ctx):
    block = DriveVideoBlock()
    config = {
        "enabled": True,
        "folder_id": "folder1",
        "fixed_caption": "Cap",
        "low_stock_threshold": 5,
        "low_stock_notified_at_remaining": None,
    }
    videos = [
        DriveVideo(f"f{i}", f"v{i}.mp4", "video/mp4", "2026-01-01")
        for i in range(3)
    ]

    with patch(
        "app.application.pipeline.blocks.drive_video.drive_allowed",
        return_value=True,
    ), patch(
        "app.application.pipeline.blocks.drive_video.list_videos",
        new=AsyncMock(return_value=videos),
    ), patch(
        "app.application.pipeline.blocks.drive_video.download_file",
        new=AsyncMock(return_value=Path("/tmp/x.mp4")),
    ), patch(
        "app.application.pipeline.blocks.drive_video.async_session_factory"
    ) as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_repo = MagicMock()
        mock_repo.get_published_file_ids = AsyncMock(return_value=set())
        with patch(
            "app.application.pipeline.blocks.drive_video.SQLADrivePublishedRepository",
            return_value=mock_repo,
        ):
            await block.execute(ctx, config)

    assert ctx.meta.get("drive_low_stock_notify") == 3
    ctx.max_client.send_message_to_user.assert_awaited()
