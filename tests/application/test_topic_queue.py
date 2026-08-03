import pytest

from app.application.pipeline.blocks.registry import BlockRegistry
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.normalize import normalize_blocks_config, steps_to_ui_dict
from app.application.pipeline.runner import PipelineRunner
from app.application.pipeline.topic_queue import (
    apply_topic_queue_remaining,
    normalize_topic_queue,
    pop_topic,
    topic_queue_from_blocks_config,
    with_preserved_topic_queue,
)


def test_normalize_topic_queue_strips_and_dedupes():
    raw = [
        "  Тема A  ",
        "",
        "- Тема B",
        "1. Тема C",
        "тема a",  # casefold dup
        "Тема B",
    ]
    assert normalize_topic_queue(raw) == ["Тема A", "Тема B", "Тема C"]


def test_normalize_topic_queue_from_multiline_string():
    assert normalize_topic_queue("Одна\n\nДве\n• Три") == ["Одна", "Две", "Три"]


def test_pop_topic_fifo():
    topic, remaining = pop_topic(["A", "B", "C"])
    assert topic == "A"
    assert remaining == ["B", "C"]
    topic2, remaining2 = pop_topic([])
    assert topic2 is None
    assert remaining2 == []


def test_apply_topic_queue_remaining_updates_story_gen():
    cfg = {
        "version": 2,
        "steps": [
            {
                "id": "s",
                "type": "story_gen",
                "enabled": True,
                "config": {"topic_queue": ["A", "B"], "target_minutes": 5},
            }
        ],
        "schedule": {"enabled": False, "frequency": "daily", "times": []},
    }
    updated = apply_topic_queue_remaining(cfg, ["B"], block_type="story_gen")
    story = updated["steps"][0]
    assert story["config"]["topic_queue"] == ["B"]


def test_apply_topic_queue_remaining_updates_post_gen():
    cfg = {
        "version": 2,
        "steps": [
            {
                "id": "1",
                "type": "post_gen",
                "enabled": True,
                "config": {
                    "mode": "ai",
                    "user_input": "бриф",
                    "topic_queue": ["A", "B"],
                },
            }
        ],
        "schedule": {"enabled": False, "frequency": "daily", "times": []},
    }
    updated = apply_topic_queue_remaining(cfg, ["B"])
    post = next(s for s in updated["steps"] if s["type"] == "post_gen")
    assert post["config"]["topic_queue"] == ["B"]
    # Original untouched
    assert cfg["steps"][0]["config"]["topic_queue"] == ["A", "B"]


def test_with_preserved_topic_queue_keeps_live_queue():
    fsm_ui = {
        "post_gen": {
            "enabled": True,
            "user_input": "бриф",
            "topic_queue": ["A", "B", "C"],  # stale session
        },
        "schedule": {"enabled": True, "times": ["10:00"]},
    }
    live = {
        "version": 2,
        "steps": [
            {
                "id": "p1",
                "type": "post_gen",
                "enabled": True,
                "config": {"topic_queue": ["B", "C"], "user_input": "old"},
            }
        ],
        "schedule": {"enabled": True, "frequency": "daily", "times": ["10:00"]},
    }
    merged = with_preserved_topic_queue(fsm_ui, live)
    assert merged["post_gen"]["topic_queue"] == ["B", "C"]
    assert merged["post_gen"]["user_input"] == "бриф"  # FSM settings kept
    assert fsm_ui["post_gen"]["topic_queue"] == ["A", "B", "C"]  # original untouched
    assert topic_queue_from_blocks_config(live) == ["B", "C"]


def test_normalize_blocks_config_keeps_topic_queue():
    v2 = normalize_blocks_config(
        {
            "post_gen": {
                "enabled": True,
                "mode": "ai",
                "user_input": "x",
                "topic_queue": ["  Тема 1 ", "", "Тема 2"],
            }
        }
    )
    post = next(s for s in v2["steps"] if s["type"] == "post_gen")
    assert post["config"]["topic_queue"] == ["Тема 1", "Тема 2"]
    ui = steps_to_ui_dict(v2)
    assert ui["post_gen"]["topic_queue"] == ["Тема 1", "Тема 2"]


@pytest.mark.asyncio
async def test_runner_uses_queued_topic_and_sets_meta():
    class _OAI:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def generate_text(self, prompt: str, system_prompt: str = "") -> str:
            self.calls.append(prompt)
            return "**Пост**\nиз очереди"

    class _Max:
        def __init__(self) -> None:
            self.user_msgs: list[dict] = []

        async def get_messages(self, chat_id, count=50):
            return [{"body": {"text": "Старая тема\nтело"}}]

        async def send_message_to_user(self, user_id, text, attachments=None, fmt=None):
            self.user_msgs.append({"user_id": user_id, "text": text})

        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            return None

    class _Channel:
        max_chat_id = 7

    class PostBlock:
        type_id = "post_gen"

        async def execute(self, ctx, config):
            return

    oai = _OAI()
    max_client = _Max()
    registry = BlockRegistry()
    registry.register(PostBlock())

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=1,
        max_client=max_client,
        openai_client=oai,
        target="channel",
        channel_title="Интерьер",
        meta={"owner_max_user_id": 99},
    )
    await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "post_gen",
                    "enabled": True,
                    "config": {
                        "mode": "ai",
                        "user_input": "бриф интерьера",
                        "topic_queue": ["Свет в гостиной", "Зеркала"],
                    },
                }
            ],
            "schedule": {"enabled": False, "frequency": "daily", "times": []},
        },
    )

    assert ctx.post_text.startswith("**Пост**")
    assert len(oai.calls) == 1  # write only, no propose/judge
    assert "Свет в гостиной" in oai.calls[0]
    assert ctx.meta["topic_queue_popped"] is True
    assert ctx.meta["topic_queue_remaining"] == ["Зеркала"]
    assert ctx.meta["topic_queue_exhausted"] is False
    assert max_client.user_msgs == []


@pytest.mark.asyncio
async def test_runner_alerts_when_last_queued_topic_used():
    class _OAI:
        async def generate_text(self, prompt: str, system_prompt: str = "") -> str:
            return "пост"

    class _Max:
        def __init__(self) -> None:
            self.user_msgs: list[dict] = []

        async def get_messages(self, chat_id, count=50):
            return []

        async def send_message_to_user(self, user_id, text, attachments=None, fmt=None):
            self.user_msgs.append({"user_id": user_id, "text": text})

    class _Channel:
        max_chat_id = 7

    class PostBlock:
        type_id = "post_gen"

        async def execute(self, ctx, config):
            return

    max_client = _Max()
    registry = BlockRegistry()
    registry.register(PostBlock())
    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=2,
        max_client=max_client,
        openai_client=_OAI(),
        target="channel",
        channel_title="Отцы и Дети",
        meta={"owner_max_user_id": 5},
    )
    await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "post_gen",
                    "enabled": True,
                    "config": {
                        "mode": "ai",
                        "user_input": "бриф",
                        "topic_queue": ["Последняя тема"],
                    },
                }
            ],
            "schedule": {"enabled": False, "frequency": "daily", "times": []},
        },
    )
    assert ctx.meta["topic_queue_exhausted"] is True
    assert ctx.meta["topic_queue_remaining"] == []
    assert len(max_client.user_msgs) == 1
    assert max_client.user_msgs[0]["user_id"] == 5
    assert "Отцы и Дети" in max_client.user_msgs[0]["text"]
    assert "закончились" in max_client.user_msgs[0]["text"].lower()


@pytest.mark.asyncio
async def test_runner_does_not_pop_queue_for_user_target():
    class _OAI:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_text(self, prompt: str, system_prompt: str = "") -> str:
            self.calls += 1
            return "ok"

    class _Max:
        async def get_messages(self, chat_id, count=50):
            return []

    class _Channel:
        max_chat_id = 1

    class PostBlock:
        type_id = "post_gen"

        async def execute(self, ctx, config):
            return

    oai = _OAI()
    registry = BlockRegistry()
    registry.register(PostBlock())
    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=None,
        max_client=_Max(),
        openai_client=oai,
        target="user",
        channel_title="Test",
        meta={"owner_max_user_id": 1},
    )
    blocks = {
        "version": 2,
        "steps": [
            {
                "id": "1",
                "type": "post_gen",
                "enabled": True,
                "config": {
                    "mode": "ai",
                    "user_input": "бриф",
                    "topic_queue": ["Не трогать"],
                },
            }
        ],
        "schedule": {"enabled": False, "frequency": "daily", "times": []},
    }
    await PipelineRunner(registry).run(ctx, blocks)
    assert ctx.meta.get("topic_queue_popped") is None
    # Still wrote a post (empty recent → single write)
    assert oai.calls == 1
