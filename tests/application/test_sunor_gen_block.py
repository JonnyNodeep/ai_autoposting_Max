"""Tests for SunorGenBlock."""

from unittest.mock import AsyncMock, patch

import pytest

from app.application.pipeline.blocks.sunor_gen import SunorGenBlock
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.sunor_service import SunorResult


@pytest.mark.asyncio
async def test_sunor_gen_block_sets_audio_path():
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=None,
        meta={},
    )
    fake = SunorResult(
        path="/tmp/track.mp3",
        task_id="t1",
        clip_id="c1",
        image_url="https://img/c.jpg",
        title="Night",
    )

    with patch(
        "app.application.pipeline.blocks.sunor_gen.generate_sunor_track",
        AsyncMock(return_value=fake),
    ):
        await SunorGenBlock().execute(
            ctx,
            {
                "enabled": True,
                "music_mode": "custom",
                "prompt": "test",
                "tags": "lullaby",
            },
        )

    assert ctx.audio_local_path == "/tmp/track.mp3"
    assert ctx.image_url == "https://img/c.jpg"
    assert ctx.meta["sunor_task_id"] == "t1"
    assert ctx.meta["sunor_clip_id"] == "c1"
