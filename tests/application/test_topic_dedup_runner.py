from unittest.mock import AsyncMock

import pytest

from app.application.pipeline.blocks.registry import BlockRegistry
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.generate_post import TopicDedupExhausted
from app.application.pipeline.runner import PipelineRunner


@pytest.mark.asyncio
async def test_runner_skips_blocks_on_topic_dedup_exhausted(monkeypatch):
    executed: list[str] = []

    class _Block:
        def __init__(self, type_id: str) -> None:
            self.type_id = type_id

        async def execute(self, ctx, config):
            executed.append(self.type_id)

    class _Max:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send_message_to_user(self, user_id, text, attachments=None, fmt=None):
            self.sent.append({"user_id": user_id, "text": text})

    async def _boom_preseed(self, ctx, v2):
        raise TopicDedupExhausted(
            channel_title="Биохакинг",
            attempts=15,
            rejected_topics=["тема A", "тема B"],
        )

    monkeypatch.setattr(PipelineRunner, "_preseed_post_text", _boom_preseed)

    registry = BlockRegistry()
    registry.register(_Block("image_prompt"))
    registry.register(_Block("image_gen"))
    registry.register(_Block("post_gen"))

    max_client = _Max()
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=10,
        max_client=max_client,
        openai_client=AsyncMock(),
        target="channel",
        channel_title="Биохакинг",
        meta={"owner_max_user_id": 42},
    )

    out = await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {"id": "1", "type": "image_prompt", "enabled": True, "config": {}},
                {"id": "2", "type": "image_gen", "enabled": True, "config": {}},
                {
                    "id": "3",
                    "type": "post_gen",
                    "enabled": True,
                    "config": {"mode": "ai", "user_input": "бриф"},
                },
            ],
            "schedule": {"enabled": False, "frequency": "daily", "times": []},
        },
    )

    assert out.post_text == ""
    assert executed == []
    assert out.meta.get("publish_skipped") == "topic_dedup"
    assert not out.meta.get("published")
    assert len(max_client.sent) == 1
    assert max_client.sent[0]["user_id"] == 42
    assert "Биохакинг" in max_client.sent[0]["text"]
    assert "15" in max_client.sent[0]["text"]
    assert "не опубликован" in max_client.sent[0]["text"].lower()
