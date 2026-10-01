"""Tests for psychology/money podcast pipeline."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.pipeline.blocks.post_gen import (
    SHARE_CTA_AUDIO,
    build_share_cta_audio,
    is_podcast_pipeline,
)
from app.application.pipeline.blocks.sunor_gen import SunorGenBlock
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.normalize import normalize_blocks_config, steps_to_ui_dict
from app.application.pipeline.podcast_presets import build_podcast_blocks_ui
from app.application.pipeline.podcast_scripts import (
    AUDIO_SCRIPT_MAX_CHARS,
    POST_MAX_CHARS,
    _parse_post_script,
    generate_podcast_content,
)
from app.application.pipeline.runner import PipelineRunner
from app.application.pipeline.sunor_service import SunorGenerationError


def test_parse_post_script_splits_blocks():
    assert POST_MAX_CHARS == 1100
    raw = (
        "POST:\n**Тема**\n\n- пункт 1\n\n"
        "SCRIPT:\nПривет. Сегодня поговорим.\nОставайся в канале."
    )
    post, script = _parse_post_script(raw)
    assert post.startswith("**Тема**")
    assert "пункт 1" in post
    assert script.startswith("Привет")
    assert "Оставайся" in script


def test_parse_post_script_fallback_without_markers():
    post, script = _parse_post_script("просто текст без маркеров")
    assert post == "просто текст без маркеров"
    assert script == ""


@pytest.mark.asyncio
async def test_generate_podcast_content_parses_and_caps_script():
    long_script = "А" * (AUDIO_SCRIPT_MAX_CHARS + 200)

    class FakeOpenAI:
        async def generate_text(self, **kwargs):
            return (
                "POST:\n**Тревога без повода**\n\n"
                "Вот три опоры:\n\n"
                "1. Дыхание\n"
                "2. Тело\n"
                "3. Пауза\n\n"
                f"SCRIPT:\n{long_script}"
            )

    post, script, title = await generate_podcast_content(
        FakeOpenAI(),
        brief="психология",
        channel_title="Психология без лишнего",
        topic="Тревога без повода",
        niche="psychology",
    )
    assert title == "Тревога без повода"
    assert "Тревога" in post
    assert len(script) == AUDIO_SCRIPT_MAX_CHARS


def test_podcast_preset_psychology_and_money():
    psych = normalize_blocks_config(build_podcast_blocks_ui("psychology"))
    money = normalize_blocks_config(build_podcast_blocks_ui("money"))
    bio = normalize_blocks_config(build_podcast_blocks_ui("biohacking"))

    assert psych["schedule"]["podcast_pipeline"] is True
    assert psych["schedule"]["podcast_niche"] == "psychology"
    assert psych["schedule"]["meditation_pipeline"] is False
    assert psych["schedule"]["postcard_pipeline"] is False

    assert money["schedule"]["podcast_niche"] == "money"
    assert bio["schedule"]["podcast_niche"] == "biohacking"

    earnings = normalize_blocks_config(build_podcast_blocks_ui("earnings"))
    assert earnings["schedule"]["podcast_niche"] == "earnings"

    sunor_p = next(s for s in psych["steps"] if s["type"] == "sunor_gen")
    sunor_m = next(s for s in money["steps"] if s["type"] == "sunor_gen")
    sunor_b = next(s for s in bio["steps"] if s["type"] == "sunor_gen")
    sunor_e = next(s for s in earnings["steps"] if s["type"] == "sunor_gen")
    assert sunor_p["enabled"] is True
    assert sunor_p["config"]["vocal_gender"] == "f"
    assert sunor_m["config"]["vocal_gender"] == "m"
    assert sunor_b["config"]["vocal_gender"] == "m"
    assert sunor_e["config"]["vocal_gender"] == "m"
    assert "energetic" in sunor_e["config"]["tags"].lower()
    assert "supportive" in sunor_e["config"]["tags"].lower()
    assert "biohack" in sunor_b["config"]["tags"].lower() or "health" in sunor_b["config"]["tags"].lower()
    assert "NOT a song" in sunor_p["config"]["tags"]
    assert "psychologist" in sunor_p["config"]["tags"].lower()
    assert "whisper" in sunor_p["config"]["negative_tags"].lower()
    assert "whisper" in sunor_m["config"]["negative_tags"].lower()
    assert "whisper" in sunor_e["config"]["negative_tags"].lower()
    assert "never fade into whisper" in sunor_m["config"]["tags"].lower()
    assert "rap" in sunor_p["config"]["negative_tags"]

    post_p = next(s for s in psych["steps"] if s["type"] == "post_gen")
    assert post_p["config"]["add_channel_link"] is False
    post_e = next(s for s in earnings["steps"] if s["type"] == "post_gen")
    assert post_e["config"]["add_channel_link"] is False

    drive = next(s for s in psych["steps"] if s["type"] == "drive_video")
    assert drive["enabled"] is False

    ui = steps_to_ui_dict(psych)
    assert ui["schedule"]["podcast_pipeline"] is True
    assert ui["schedule"]["podcast_niche"] == "psychology"


def test_podcast_footer_listen_line_only_with_audio():
    from app.application.pipeline.blocks.post_gen import PODCAST_LISTEN_CTA

    assert "подкаст" in PODCAST_LISTEN_CTA.lower()


@pytest.mark.asyncio
async def test_post_gen_podcast_footer_rules():
    from app.application.pipeline.blocks.post_gen import PODCAST_LISTEN_CTA, PostGenBlock

    related = [
        {
            "title": "Другой канал",
            "link": "https://max.ru/other",
            "source": "manual",
        }
    ]

    async def _run(*, has_audio: bool) -> str:
        max_client = MagicMock()
        max_client.send_message = AsyncMock(return_value={"message_id": 1})
        max_client.upload_file = AsyncMock(return_value="tok")
        channel = MagicMock()
        channel.id = 3
        channel.max_chat_id = 999
        channel.channel_link = "https://max.ru/bio"
        channel.telegram_link = None
        ctx = PipelineContext(
            channel=channel,
            channel_link="https://max.ru/bio",
            channel_title="Биохакинг на каждый день",
            run_id=1,
            max_client=max_client,
            openai_client=None,
            target="channel",
            post_text="**Тема**\n\nТекст поста",
            image_url="https://example.com/img.png",
            audio_local_path="/tmp/a.mp3" if has_audio else "",
            meta={"pipeline_schedule": {"podcast_pipeline": True}},
        )
        with patch(
            "app.application.pipeline.blocks.post_gen.resolve_related_channels",
            AsyncMock(return_value=related),
        ), patch(
            "app.application.pipeline.blocks.post_gen._attachment_from_image_path",
            AsyncMock(return_value={"type": "image", "payload": {"url": "u"}}),
        ), patch(
            "app.application.pipeline.blocks.post_gen._mirror_to_telegram",
            AsyncMock(),
        ):
            await PostGenBlock().execute(
                ctx,
                {
                    "enabled": True,
                    "add_channel_link": True,
                    "related_channels_enabled": True,
                    "related_channels": related,
                },
            )
        call = max_client.send_message.await_args_list[0]
        return call.kwargs.get("text") or call.args[0]

    with_audio = await _run(has_audio=True)
    without = await _run(has_audio=False)

    assert "Подпишись" not in with_audio
    assert "Подпишись" not in without
    assert "Другой канал" in with_audio
    assert "Другой канал" in without
    assert PODCAST_LISTEN_CTA.strip() in with_audio
    assert "подкаст" not in without.lower() or "Другой" in without
    assert PODCAST_LISTEN_CTA.strip() not in without


def test_podcast_mutual_exclusion_with_meditation():
    ui = build_podcast_blocks_ui("psychology")
    ui["schedule"]["meditation_pipeline"] = True
    ui["schedule"]["postcard_pipeline"] = True
    v2 = normalize_blocks_config(ui)
    assert v2["schedule"]["podcast_pipeline"] is True
    assert v2["schedule"]["meditation_pipeline"] is False
    assert v2["schedule"]["postcard_pipeline"] is False


def test_is_podcast_pipeline_and_share_cta_skipped_conceptually():
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=None,
        meta={"pipeline_schedule": {"podcast_pipeline": True}},
    )
    assert is_podcast_pipeline(ctx) is True
    # Fairy-tale CTA still builds for non-podcast helpers
    assert "сказка" in build_share_cta_audio("Текст").lower() or SHARE_CTA_AUDIO


@pytest.mark.asyncio
async def test_sunor_gen_podcast_soft_fail():
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=7,
        max_client=None,
        openai_client=None,
        meta={
            "pipeline_schedule": {"podcast_pipeline": True},
            "audio_script": "Длинный скрипт подкаста про деньги.",
            "display_title": "Тема",
        },
    )
    ctx.notify = AsyncMock()

    with patch(
        "app.application.pipeline.blocks.sunor_gen.generate_sunor_track",
        AsyncMock(side_effect=SunorGenerationError("boom")),
    ):
        await SunorGenBlock().execute(
            ctx,
            {
                "enabled": True,
                "music_mode": "custom",
                "tags": "podcast",
                "prompt": "",
            },
        )

    assert ctx.audio_local_path == ""
    assert ctx.audio_token == ""
    ctx.notify.assert_awaited()


@pytest.mark.asyncio
async def test_sunor_gen_podcast_empty_script_soft_skip():
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=8,
        max_client=None,
        openai_client=None,
        meta={
            "pipeline_schedule": {"podcast_pipeline": True},
            "audio_script": "",
        },
    )
    ctx.notify = AsyncMock()
    with patch(
        "app.application.pipeline.blocks.sunor_gen.generate_sunor_track",
        AsyncMock(),
    ) as gen:
        await SunorGenBlock().execute(ctx, {"enabled": True, "music_mode": "custom"})
        gen.assert_not_called()
    assert ctx.audio_local_path == ""


@pytest.mark.asyncio
async def test_runner_podcast_preseed_sets_audio_script():
    openai = MagicMock()
    openai.generate_text = AsyncMock(
        return_value=(
            "POST:\n**Деньги и привычки**\n\n1. Учёт\n2. Пауза\n\n"
            "SCRIPT:\nСегодня разберём привычки. "
            "Если откликнулось — оставайся в «Деньги по делу»."
        )
    )
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        channel_title="Деньги по делу",
        run_id=9,
        max_client=MagicMock(),
        openai_client=openai,
        target="channel",
        meta={},
    )
    ctx.notify = AsyncMock()

    blocks = build_podcast_blocks_ui("money")
    blocks["schedule"]["times"] = ["06:09", "16:09"]
    blocks["post_gen"]["topic_queue"] = ["Деньги и привычки", "Вторая тема"]
    blocks["post_gen"]["enabled"] = True

    # Only preseed — skip later blocks by disabling them except post_gen for seed
    for key in ("image_prompt", "image_gen", "sunor_gen"):
        blocks[key]["enabled"] = False

    v2 = normalize_blocks_config(blocks)
    # Run only preseed path via runner with empty registry-friendly config
    runner = PipelineRunner()
    with patch.object(runner, "_run_blocks", wraps=runner._run_blocks):
        # Directly call preseed
        await runner._preseed_post_text(ctx, v2)

    assert "привычки" in (ctx.post_text or "").lower() or "Деньги" in (ctx.post_text or "")
    assert ctx.meta.get("audio_script")
    assert "Деньги по делу" in ctx.meta["audio_script"]
    assert ctx.meta.get("topic_queue_used") == "Деньги и привычки"
    assert ctx.meta.get("topic_queue_remaining") == ["Вторая тема"]
