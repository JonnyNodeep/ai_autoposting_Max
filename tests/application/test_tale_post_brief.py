"""Tests for post_gen brief on fairy-tale video publishes."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.application.pipeline.blocks.post_gen import PostGenBlock
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.generate_post import (
    _tale_post_max_chars,
    _trim_tale_post,
    generate_tale_post_caption,
)
from app.application.pipeline.normalize import resolve_post_brief
from app.application.pipeline.tale_video import TaleScene, TaleScript


def test_tale_post_max_chars_parsed_from_brief():
    brief = "Превью сказки, общий пост не больше 300 символов"
    assert _tale_post_max_chars(brief) == 300


def test_tale_post_max_chars_default():
    assert _tale_post_max_chars("Короткий анонс") == 400


def test_trim_tale_post():
    text = " ".join(["слово"] * 80)
    out = _trim_tale_post(text, 120)
    assert len(out) <= 120
    assert out.endswith(".")


@pytest.mark.asyncio
async def test_generate_tale_post_caption_calls_openai():
    client = AsyncMock()
    client.generate_text = AsyncMock(return_value="Бот в шапке профиля поможет с персональной сказкой.")

    out = await generate_tale_post_caption(
        client,
        "Превью и бот в шапке, не больше 300 символов",
        "Аудиосказки",
        tale_title="Егор",
        tale_caption="Сказка про очередь",
        story_excerpt="Жил-был Егор.",
    )
    assert "бот" in out.lower()
    client.generate_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_gen_tale_brief_overwrites_caption(monkeypatch):
    sent: list[str] = []

    class _Max:
        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append(text)

    class _Channel:
        max_chat_id = 42

    async def _fake_caption(*args, **kwargs):
        return "Текст с ботом в шапке профиля для персональной сказки."

    monkeypatch.setattr(
        "app.application.pipeline.generate_post.generate_tale_post_caption",
        _fake_caption,
    )

    script = TaleScript(
        title="Егор",
        caption="Старый caption от story_gen",
        story="Жил-был Егор на горке.",
        scenes=[TaleScene(id=1, story_span="s", image_prompt_en="p")],
    )
    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=73,
        max_client=_Max(),
        openai_client=AsyncMock(),
        target="channel",
        channel_title="Аудиосказки",
        post_text=script.caption,
        story_script=script.story,
        video_token="vid-token",
        meta={"tale_script": script.to_meta(), "pipeline_schedule": {}},
    )

    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "ai",
            "user_input": "Превью и бот в шапке, не больше 300 символов",
            "add_channel_link": False,
        },
    )

    assert "бот" in ctx.post_text.lower()
    assert len(sent) == 1
    assert "бот" in sent[0].lower()


@pytest.mark.asyncio
async def test_post_gen_tale_empty_brief_keeps_story_caption(monkeypatch):
    sent: list[str] = []

    class _Max:
        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append(text)

    class _Channel:
        max_chat_id = 42

    fake = AsyncMock(return_value="should not be used")
    monkeypatch.setattr(
        "app.application.pipeline.generate_post.generate_tale_post_caption",
        fake,
    )

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=73,
        max_client=_Max(),
        openai_client=AsyncMock(),
        target="channel",
        post_text="Caption от story_gen",
        video_token="vid-token",
        meta={"pipeline_schedule": {}},
    )

    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "ai",
            "user_input": "",
            "add_channel_link": False,
        },
    )

    fake.assert_not_awaited()
    assert ctx.post_text == "Caption от story_gen"
    assert sent == ["Caption от story_gen"]


def test_resolve_post_brief_slot_prompt_for_tale_post():
    schedule = {
        "per_slot_prompts": True,
        "slot_prompts": {"15:45": "Только для слота: бот в шапке"},
    }
    post_cfg = {"user_input": "Общий бриф поста"}
    assert resolve_post_brief(schedule, post_cfg, "15:45") == "Только для слота: бот в шапке"


@pytest.mark.asyncio
async def test_post_gen_tale_uses_slot_brief(monkeypatch):
    captured: dict[str, str] = {}

    class _Max:
        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            return None

    class _Channel:
        max_chat_id = 42

    async def _capture(*args, **kwargs):
        captured["brief"] = kwargs.get("brief") or args[1]
        return "Слотный пост про бота в шапке."

    monkeypatch.setattr(
        "app.application.pipeline.generate_post.generate_tale_post_caption",
        _capture,
    )

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=73,
        max_client=_Max(),
        openai_client=AsyncMock(),
        target="channel",
        post_text="Caption",
        video_token="vid-token",
        meta={
            "slot_time": "15:45",
            "pipeline_schedule": {
                "per_slot_prompts": True,
                "slot_prompts": {"15:45": "Только для слота: бот в шапке"},
            },
        },
    )

    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "ai",
            "user_input": "Общий бриф поста",
            "add_channel_link": False,
        },
    )

    assert captured["brief"] == "Только для слота: бот в шапке"
    assert "бот" in ctx.post_text.lower()
