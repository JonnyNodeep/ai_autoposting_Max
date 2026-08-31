import json
from pathlib import Path

import pytest

from app.application.pipeline.normalize import (
    mix_slot_brief,
    mix_slot_image_addon,
    normalize_blocks_config,
    normalize_related_channels,
    resolve_post_brief,
    resolve_slot_image_addon,
    steps_to_ui_dict,
    ui_dict_to_v2,
    is_v2,
)
from app.application.pipeline.runner import PipelineRunner
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.blocks.registry import BlockRegistry
from app.application.pipeline.blocks.image_prompt import ImagePromptBlock
from app.application.pipeline.blocks.image_gen import ImageGenBlock
from app.application.pipeline.blocks.post_gen import (
    PostGenBlock,
    RELATED_CHANNELS_INTRO,
    build_related_channels_footer,
    build_subscribe_cta,
)
from app.bot.states.ai_studio import DEFAULT_BLOCKS


def test_normalize_legacy_dict_to_v2():
    v2 = normalize_blocks_config(DEFAULT_BLOCKS)
    assert v2["version"] == 2
    assert [s["type"] for s in v2["steps"]] == [
        "story_gen",
        "image_prompt",
        "image_gen",
        "video_gen",
        "tts_gen",
        "post_gen",
    ]
    assert "schedule" in v2
    assert v2["schedule"]["enabled"] is False
    assert v2["schedule"]["per_slot_prompts"] is False
    assert v2["schedule"]["slot_prompts"] == {}
    assert v2["schedule"]["slot_prompt_modes"] == {}
    assert v2["schedule"]["slot_image_addons"] == {}
    assert "news_rss" in v2
    assert v2["news_rss"]["enabled"] is False
    assert is_v2(v2)


def test_ui_roundtrip_preserves_fields():
    ui = {
        "image_prompt": {
            "enabled": True,
            "mode": "from_post",
            "user_description": "",
            "generated_prompt": "",
            "instruction": "Сгенерируй картинку для этого поста",
        },
        "image_gen": {
            "enabled": True,
            "model": "gpt-image-2",
            "add_watermark": False,
            "allow_text": False,
        },
        "video_gen": {
            "enabled": False,
            "model": "seedance-1.5-pro",
            "duration": 4,
            "mode": "normal",
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "fixed_lens": False,
            "generate_audio": False,
            "fallback_model": "wan2.2-image-to-video-fast",
            "prompt_mode": "ai",
            "user_description": "",
            "generated_prompt": "",
        },
        "post_gen": {
            "enabled": True,
            "mode": "ai",
            "user_input": "бриф рецептов",
            "generated_post": "",
            "add_channel_link": True,
            "bold_headings": False,
            "use_emoji": True,
            "comments_enabled": False,
        },
        "schedule": {
            "enabled": True,
            "frequency": "3x_day",
            "times": ["05:00", "10:00"],
            "per_slot_prompts": True,
            "slot_prompts": {"05:00": "Гороскоп на день"},
        },
    }
    v2 = ui_dict_to_v2(ui)
    back = steps_to_ui_dict(v2)
    assert back["image_prompt"]["mode"] == "from_post"
    assert back["image_prompt"]["instruction"] == "Сгенерируй картинку для этого поста"
    assert back["post_gen"]["user_input"] == "бриф рецептов"
    assert back["post_gen"]["bold_headings"] is False
    assert back["post_gen"]["use_emoji"] is True
    assert back["post_gen"]["comments_enabled"] is False
    assert back["schedule"]["times"] == ["05:00", "10:00"]
    assert back["schedule"]["per_slot_prompts"] is True
    assert back["schedule"]["slot_prompts"] == {"05:00": "Гороскоп на день"}
    assert back["schedule"]["slot_prompt_modes"] == {}
    assert back["schedule"]["slot_image_addons"] == {}
    assert back["image_gen"]["model"] == "gpt-image-2"
    assert back["image_gen"]["add_watermark"] is False
    assert back["image_gen"]["allow_text"] is False
    assert back["video_gen"]["model"] == "seedance-1.5-pro"
    assert back["video_gen"]["duration"] == 4
    assert back["video_gen"]["aspect_ratio"] == "9:16"
    assert back["video_gen"]["fallback_model"] == "wan2.2-image-to-video-fast"
    assert back["video_gen"]["generate_audio"] is False


def test_normalize_schedule_migrates_legacy_times():
    v2 = normalize_blocks_config(
        {
            "version": 2,
            "steps": [],
            "schedule": {"enabled": True, "frequency": "2x_day", "times": ["05:00", "12:00"]},
        }
    )
    assert v2["schedule"]["per_slot_prompts"] is False
    assert v2["schedule"]["slot_prompts"] == {}
    assert v2["schedule"]["slot_prompt_modes"] == {}
    assert v2["schedule"]["slot_image_addons"] == {}
    assert v2["schedule"]["times"] == ["05:00", "12:00"]


def test_normalize_schedule_drops_unknown_slot_prompts_and_empty():
    v2 = normalize_blocks_config(
        {
            "version": 2,
            "steps": [],
            "schedule": {
                "enabled": True,
                "frequency": "2x_day",
                "times": ["05:00", "12:00"],
                "per_slot_prompts": True,
                "slot_prompts": {
                    "05:00": "  утро  ",
                    "12:00": "",
                    "99:00": "лишний",
                },
            },
        }
    )
    assert v2["schedule"]["slot_prompts"] == {"05:00": "утро"}
    assert v2["schedule"]["slot_prompt_modes"] == {}


def test_normalize_schedule_keeps_append_modes_and_drops_junk():
    v2 = normalize_blocks_config(
        {
            "version": 2,
            "steps": [],
            "schedule": {
                "enabled": True,
                "frequency": "2x_day",
                "times": ["05:00", "12:00"],
                "per_slot_prompts": True,
                "slot_prompts": {
                    "05:00": "утром без рекламы",
                    "12:00": "Гороскоп",
                },
                "slot_prompt_modes": {
                    "05:00": "append",
                    "12:00": "replace",
                    "18:00": "append",
                    "99:00": "append",
                },
            },
        }
    )
    assert v2["schedule"]["slot_prompts"] == {
        "05:00": "утром без рекламы",
        "12:00": "Гороскоп",
    }
    assert v2["schedule"]["slot_prompt_modes"] == {"05:00": "append"}


def test_normalize_schedule_drops_modes_when_per_slot_disabled():
    v2 = normalize_blocks_config(
        {
            "version": 2,
            "steps": [],
            "schedule": {
                "enabled": True,
                "frequency": "daily",
                "times": ["05:00"],
                "per_slot_prompts": False,
                "slot_prompts": {"05:00": "X"},
                "slot_prompt_modes": {"05:00": "append"},
                "slot_image_addons": {"05:00": "на картинке котики"},
            },
        }
    )
    assert v2["schedule"]["slot_prompts"] == {}
    assert v2["schedule"]["slot_prompt_modes"] == {}
    assert v2["schedule"]["slot_image_addons"] == {}


def test_ui_roundtrip_preserves_slot_prompt_modes():
    ui = {
        "post_gen": {
            "enabled": True,
            "mode": "ai",
            "user_input": "общий бриф",
        },
        "schedule": {
            "enabled": True,
            "frequency": "2x_day",
            "times": ["05:00", "12:00"],
            "per_slot_prompts": True,
            "slot_prompts": {
                "05:00": "утром без рекламы",
                "12:00": "Гороскоп",
            },
            "slot_prompt_modes": {
                "05:00": "append",
                "12:00": "replace",
            },
        },
    }
    back = steps_to_ui_dict(ui_dict_to_v2(ui))
    assert back["schedule"]["slot_prompts"] == {
        "05:00": "утром без рекламы",
        "12:00": "Гороскоп",
    }
    assert back["schedule"]["slot_prompt_modes"] == {"05:00": "append"}


def test_normalize_schedule_keeps_image_addons_without_slot_prompt():
    v2 = normalize_blocks_config(
        {
            "version": 2,
            "steps": [],
            "schedule": {
                "enabled": True,
                "frequency": "2x_day",
                "times": ["05:00", "12:00"],
                "per_slot_prompts": True,
                "slot_prompts": {},
                "slot_image_addons": {
                    "05:00": "  на картинке котики  ",
                    "12:00": "",
                    "99:00": "лишний",
                },
            },
        }
    )
    assert v2["schedule"]["per_slot_prompts"] is True
    assert v2["schedule"]["slot_prompts"] == {}
    assert v2["schedule"]["slot_image_addons"] == {"05:00": "на картинке котики"}


def test_ui_roundtrip_preserves_slot_image_addons():
    ui = {
        "post_gen": {"enabled": True, "mode": "ai", "user_input": "общий бриф"},
        "schedule": {
            "enabled": True,
            "frequency": "2x_day",
            "times": ["05:00", "12:00"],
            "per_slot_prompts": True,
            "slot_prompts": {},
            "slot_image_addons": {"05:00": "на картинке котики"},
        },
    }
    back = steps_to_ui_dict(ui_dict_to_v2(ui))
    assert back["schedule"]["slot_prompts"] == {}
    assert back["schedule"]["slot_image_addons"] == {"05:00": "на картинке котики"}


def test_resolve_post_brief_fallback_and_slot():
    schedule = {
        "per_slot_prompts": True,
        "slot_prompts": {"05:00": "Гороскоп"},
    }
    post = {"user_input": "Общий бриф"}
    assert resolve_post_brief(schedule, post, "05:00") == "Гороскоп"
    assert resolve_post_brief(schedule, post, "12:00") == "Общий бриф"
    assert resolve_post_brief({"per_slot_prompts": False, "slot_prompts": {"05:00": "X"}}, post, "05:00") == "Общий бриф"
    assert resolve_post_brief(None, post, "05:00") == "Общий бриф"


def test_mix_slot_brief_joins_or_falls_back():
    mixed = mix_slot_brief("Общий бриф", "утром без рекламы")
    assert mixed.startswith("Общий бриф")
    assert "утром без рекламы" in mixed
    assert "важнее общего брифа" in mixed
    assert mix_slot_brief("Общий бриф", "  ") == "Общий бриф"
    assert mix_slot_brief("", "только слот") == "только слот"
    assert mix_slot_brief("  ", "  ") == ""


def test_mix_and_resolve_slot_image_addon():
    mixed = mix_slot_image_addon("тема поста", "на картинке котики")
    assert mixed.startswith("тема поста")
    assert "на картинке котики" in mixed
    assert "важнее общей инструкции" in mixed
    assert mix_slot_image_addon("тема", "  ") == "тема"
    assert mix_slot_image_addon("", "котики") == "котики"

    schedule = {
        "per_slot_prompts": True,
        "slot_image_addons": {"05:00": "на картинке котики"},
    }
    assert resolve_slot_image_addon(schedule, "05:00") == "на картинке котики"
    assert resolve_slot_image_addon(schedule, "12:00") == ""
    assert resolve_slot_image_addon({"per_slot_prompts": False, "slot_image_addons": {"05:00": "X"}}, "05:00") == ""
    assert resolve_slot_image_addon(None, "05:00") == ""


def test_resolve_post_brief_append_mixes_general():
    schedule = {
        "per_slot_prompts": True,
        "slot_prompts": {"05:00": "утром без рекламы"},
        "slot_prompt_modes": {"05:00": "append"},
    }
    post = {"user_input": "Общий бриф"}
    mixed = resolve_post_brief(schedule, post, "05:00")
    assert "Общий бриф" in mixed
    assert "утром без рекламы" in mixed
    assert resolve_post_brief(schedule, post, "12:00") == "Общий бриф"
    assert resolve_post_brief(
        {**schedule, "slot_prompts": {"05:00": ""}},
        post,
        "05:00",
    ) == "Общий бриф"


@pytest.mark.asyncio
async def test_post_gen_uses_slot_prompt_when_enabled():
    class _OAI:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def generate_text(self, prompt: str, system_prompt: str = "") -> str:
            self.prompts.append(prompt)
            return "Готовый пост"

    class _Max:
        async def get_messages(self, chat_id, count=50):
            return []

        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            return None

    class _Channel:
        max_chat_id = 1

    oai = _OAI()
    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=3,
        max_client=_Max(),
        openai_client=oai,
        target="channel",
        channel_title="Астро",
        meta={"slot_time": "05:00"},
    )
    await PipelineRunner().run(
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
                        "user_input": "Общий дневной контент",
                        "add_channel_link": False,
                    },
                },
            ],
            "schedule": {
                "enabled": True,
                "frequency": "2x_day",
                "times": ["05:00", "12:00"],
                "per_slot_prompts": True,
                "slot_prompts": {"05:00": "Гороскоп на сегодня"},
            },
        },
    )
    assert len(oai.prompts) == 1
    assert "Гороскоп на сегодня" in oai.prompts[0]
    assert "Общий дневной контент" not in oai.prompts[0]
    assert ctx.post_text == "Готовый пост"


@pytest.mark.asyncio
async def test_post_gen_appends_slot_addon_to_general_brief():
    class _OAI:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def generate_text(self, prompt: str, system_prompt: str = "") -> str:
            self.prompts.append(prompt)
            return "Готовый пост"

    class _Max:
        async def get_messages(self, chat_id, count=50):
            return []

        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            return None

    class _Channel:
        max_chat_id = 1

    oai = _OAI()
    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=4,
        max_client=_Max(),
        openai_client=oai,
        target="channel",
        channel_title="Астро",
        meta={"slot_time": "05:00"},
    )
    await PipelineRunner().run(
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
                        "user_input": "Общий дневной контент",
                        "add_channel_link": False,
                    },
                },
            ],
            "schedule": {
                "enabled": True,
                "frequency": "2x_day",
                "times": ["05:00", "12:00"],
                "per_slot_prompts": True,
                "slot_prompts": {"05:00": "утром без рекламы"},
                "slot_prompt_modes": {"05:00": "append"},
            },
        },
    )
    joined = "\n".join(oai.prompts)
    assert oai.prompts
    assert "Общий дневной контент" in joined
    assert "утром без рекламы" in joined
    assert ctx.post_text == "Готовый пост"


@pytest.mark.asyncio
async def test_video_gen_calls_generate_with_fallback(tmp_path):
    from unittest.mock import AsyncMock, patch

    from app.application.pipeline.blocks.video_gen import VideoGenBlock

    captured: dict = {}

    class FakeVidGo:
        async def generate_video_with_fallback(self, **kwargs):
            captured["kwargs"] = kwargs
            return {"files": [{"file_url": "https://vidgo/out.mp4"}]}

        async def close(self):
            captured["closed"] = True

    class FakeMax:
        async def upload_file(self, path, kind):
            captured["upload"] = (path, kind)
            return "max-video-token"

        async def send_message_to_user(self, **kwargs):
            pass

    class FakeResponse:
        content = b"fake-mp4"

        def raise_for_status(self):
            return None

    class FakeHttp:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url):
            assert url == "https://vidgo/out.mp4"
            return FakeResponse()

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=FakeMax(),
        openai_client=None,
        target="channel",
    )
    ctx.image_url = "https://cdn/img.png"
    ctx.notify = AsyncMock()

    with (
        patch(
            "app.infrastructure.services.vidgo_client.VidGoClient",
            return_value=FakeVidGo(),
        ),
        patch("app.application.pipeline.blocks.video_gen.httpx.AsyncClient", FakeHttp),
        patch("app.application.pipeline.blocks.video_gen.UPLOAD_DIR", tmp_path),
    ):
        await VideoGenBlock().execute(
            ctx,
            {
                "enabled": True,
                "model": "seedance-1.5-pro",
                "generated_prompt": "slow zoom",
                "fallback_model": "wan2.2-image-to-video-fast",
            },
        )

    assert captured["kwargs"]["prompt"] == "slow zoom"
    assert captured["kwargs"]["image_url"] == "https://cdn/img.png"
    assert captured["kwargs"]["config"]["model"] == "seedance-1.5-pro"
    assert ctx.video_token == "max-video-token"
    assert captured.get("closed") is True
    assert Path(ctx.video_local_path).exists()
    assert Path(ctx.video_local_path).parent == tmp_path
    assert captured["upload"][1] == "video"


@pytest.mark.asyncio
async def test_runner_skips_unknown_and_runs_enabled_order(monkeypatch):
    from app.config import settings
    from app.application.auth import feature_access as fa

    monkeypatch.setattr(settings.features, "video_whitelist", "10")
    fa._video_whitelist.cache_clear()

    calls: list[str] = []

    class _Block:
        def __init__(self, type_id: str) -> None:
            self.type_id = type_id

        async def execute(self, ctx, config):
            calls.append(self.type_id)

    registry = BlockRegistry()
    for t in ("image_prompt", "image_gen", "video_gen", "post_gen"):
        registry.register(_Block(t))

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=None,
        meta={"owner_max_user_id": 10},
    )
    config = {
        "version": 2,
        "steps": [
            {"id": "a", "type": "image_prompt", "enabled": True, "config": {}},
            {"id": "b", "type": "image_gen", "enabled": False, "config": {}},
            {"id": "c", "type": "video_gen", "enabled": True, "config": {}},
            {"id": "d", "type": "post_gen", "enabled": True, "config": {}},
            {"id": "e", "type": "unknown_future", "enabled": True, "config": {}},
        ],
        "schedule": {"enabled": False, "frequency": "daily", "times": []},
    }
    await PipelineRunner(registry).run(ctx, config)
    assert calls == ["image_prompt", "image_gen", "video_gen", "post_gen"]


@pytest.mark.asyncio
async def test_image_prompt_feeds_image_gen():
    class PromptBlock:
        type_id = "image_prompt"

        async def execute(self, ctx, config):
            ctx.image_prompt = config.get("generated_prompt", "")

    class GenBlock:
        type_id = "image_gen"

        async def execute(self, ctx, config):
            if ctx.image_prompt:
                ctx.image_url = f"img:{ctx.image_prompt}"

    registry = BlockRegistry()
    registry.register(PromptBlock())
    registry.register(GenBlock())

    class _OAI:
        pass

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=None,
        max_client=None,
        openai_client=_OAI(),
    )
    await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "image_prompt",
                    "enabled": True,
                    "config": {"generated_prompt": "sunset"},
                },
                {"id": "2", "type": "image_gen", "enabled": True, "config": {}},
            ],
            "schedule": {"enabled": False, "frequency": "daily", "times": []},
        },
    )
    assert ctx.image_url == "img:sunset"


@pytest.mark.asyncio
async def test_image_prompt_from_post_uses_seeded_post():
    class GenBlock:
        type_id = "image_gen"

        async def execute(self, ctx, config):
            if ctx.image_prompt:
                ctx.image_url = f"img:{ctx.image_prompt[:40]}"

    registry = BlockRegistry()
    registry.register(ImagePromptBlock())
    registry.register(GenBlock())

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=7,
        max_client=None,
        openai_client=None,
        target="channel",
    )
    await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "image_prompt",
                    "enabled": True,
                    "config": {
                        "mode": "from_post",
                        "instruction": "Сгенерируй картинку для этого поста",
                    },
                },
                {"id": "2", "type": "image_gen", "enabled": True, "config": {}},
                {
                    "id": "3",
                    "type": "post_gen",
                    "enabled": True,
                    "config": {
                        "mode": "fixed",
                        "generated_post": "Салат с киноа и авокадо",
                    },
                },
            ],
            "schedule": {"enabled": False, "frequency": "daily", "times": []},
        },
    )
    assert "Салат с киноа и авокадо" in ctx.image_prompt
    assert "Сгенерируй картинку для этого поста" in ctx.image_prompt
    assert ctx.image_url.startswith("img:")


@pytest.mark.asyncio
async def test_image_prompt_from_topic_uses_meta_topic_not_full_post():
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=71,
        max_client=None,
        openai_client=None,
        target="channel",
        post_text="🌷 Пионы в вазе\n\nДлинный текст про уход за цветами и полив.",
        meta={"post_topic": "Пионы в вазе"},
    )
    await ImagePromptBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "from_topic",
            "instruction": "Сгенерируй картинку по этой теме",
            "use_visual_style": False,
        },
    )
    assert "Пионы в вазе" in ctx.image_prompt
    assert "Сгенерируй картинку по этой теме" in ctx.image_prompt
    assert "Длинный текст про уход" not in ctx.image_prompt


@pytest.mark.asyncio
async def test_image_prompt_from_topic_mixes_slot_image_addon():
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=74,
        max_client=None,
        openai_client=None,
        target="channel",
        post_text="🌷 Пионы в вазе\n\nДлинный текст про уход.",
        meta={
            "post_topic": "Пионы в вазе",
            "slot_time": "05:00",
            "pipeline_schedule": {
                "per_slot_prompts": True,
                "slot_image_addons": {"05:00": "на картинке котики"},
            },
        },
    )
    await ImagePromptBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "from_topic",
            "instruction": "Сгенерируй картинку по этой теме",
            "use_visual_style": False,
        },
    )
    assert "Пионы в вазе" in ctx.image_prompt
    assert "на картинке котики" in ctx.image_prompt
    assert "Длинный текст про уход" not in ctx.image_prompt


@pytest.mark.asyncio
async def test_image_prompt_from_topic_ignores_addon_without_slot_time():
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=75,
        max_client=None,
        openai_client=None,
        target="channel",
        post_text="Пионы в вазе",
        meta={
            "post_topic": "Пионы в вазе",
            "pipeline_schedule": {
                "per_slot_prompts": True,
                "slot_image_addons": {"05:00": "на картинке котики"},
            },
        },
    )
    await ImagePromptBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "from_topic",
            "instruction": "Сгенерируй картинку по этой теме",
            "use_visual_style": False,
        },
    )
    assert "Пионы в вазе" in ctx.image_prompt
    assert "котики" not in ctx.image_prompt


@pytest.mark.asyncio
async def test_image_prompt_from_topic_fallback_first_line():
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=72,
        max_client=None,
        openai_client=None,
        target="channel",
        post_text="🌹 Розы на окне\n\nПодробный рецепт ухода за розами дома.",
    )
    await ImagePromptBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "from_topic",
            "instruction": "Сгенерируй картинку по этой теме",
            "use_visual_style": False,
        },
    )
    assert "🌹 Розы на окне" in ctx.image_prompt
    assert "Подробный рецепт ухода" not in ctx.image_prompt


@pytest.mark.asyncio
async def test_image_prompt_from_topic_via_runner_seeded_post():
    class GenBlock:
        type_id = "image_gen"

        async def execute(self, ctx, config):
            if ctx.image_prompt:
                ctx.image_url = f"img:{ctx.image_prompt[:40]}"

    registry = BlockRegistry()
    registry.register(ImagePromptBlock())
    registry.register(GenBlock())

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=73,
        max_client=None,
        openai_client=None,
        target="channel",
    )
    await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "image_prompt",
                    "enabled": True,
                    "config": {
                        "mode": "from_topic",
                        "instruction": "Сгенерируй картинку по этой теме",
                        "use_visual_style": False,
                    },
                },
                {"id": "2", "type": "image_gen", "enabled": True, "config": {}},
                {
                    "id": "3",
                    "type": "post_gen",
                    "enabled": True,
                    "config": {
                        "mode": "fixed",
                        "generated_post": "Салат с киноа и авокадо\n\nИнгредиенты и шаги приготовления.",
                    },
                },
            ],
            "schedule": {"enabled": False, "frequency": "daily", "times": []},
        },
    )
    assert ctx.meta.get("post_topic") == "Салат с киноа и авокадо"
    assert "Салат с киноа и авокадо" in ctx.image_prompt
    assert "Ингредиенты и шаги" not in ctx.image_prompt
    assert ctx.image_url.startswith("img:")


@pytest.mark.asyncio
async def test_runner_from_topic_uses_slot_image_addon_not_post_brief():
    class GenBlock:
        type_id = "image_gen"

        async def execute(self, ctx, config):
            if ctx.image_prompt:
                ctx.image_url = f"img:{ctx.image_prompt[:40]}"

    registry = BlockRegistry()
    registry.register(ImagePromptBlock())
    registry.register(GenBlock())

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=76,
        max_client=None,
        openai_client=None,
        target="channel",
        meta={"slot_time": "05:00"},
    )
    await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "image_prompt",
                    "enabled": True,
                    "config": {
                        "mode": "from_topic",
                        "instruction": "Сгенерируй картинку по этой теме",
                        "use_visual_style": False,
                    },
                },
                {"id": "2", "type": "image_gen", "enabled": True, "config": {}},
                {
                    "id": "3",
                    "type": "post_gen",
                    "enabled": True,
                    "config": {
                        "mode": "fixed",
                        "user_input": "Общий бриф без животных",
                        "generated_post": "Салат с киноа и авокадо\n\nИнгредиенты и шаги приготовления.",
                    },
                },
            ],
            "schedule": {
                "enabled": True,
                "frequency": "2x_day",
                "times": ["05:00", "12:00"],
                "per_slot_prompts": True,
                "slot_prompts": {},
                "slot_image_addons": {"05:00": "на картинке котики"},
            },
        },
    )
    assert "Салат с киноа и авокадо" in ctx.image_prompt
    assert "на картинке котики" in ctx.image_prompt
    assert "Общий бриф без животных" not in ctx.image_prompt
    assert "котики" not in (ctx.post_text or "")


@pytest.mark.asyncio
async def test_post_gen_ai_preseed_generates_each_run():
    class _OAI:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def generate_text(self, prompt: str, system_prompt: str = "") -> str:
            self.calls.append({"prompt": prompt, "system": system_prompt})
            return "**Салат с киноа**\n\nНовый рецепт по брифу"

    class GenBlock:
        type_id = "image_gen"

        async def execute(self, ctx, config):
            if ctx.image_prompt:
                ctx.image_url = "https://example.com/x.png"

    sent: list[dict] = []

    class _Max:
        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append({"text": text, "attachments": attachments})

        async def get_messages(self, chat_id, count=50):
            return [
                {"body": {"text": "🍲 Гречка с индейкой и овощами — ПП-ужин\n\nТекст..."}},
                {"body": {"text": "🥗 Салат с тунцом\n\nЕщё текст"}},
            ]

    class _Channel:
        max_chat_id = 11

    oai = _OAI()
    registry = BlockRegistry()
    registry.register(ImagePromptBlock())
    registry.register(GenBlock())
    registry.register(PostGenBlock())

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=9,
        max_client=_Max(),
        openai_client=oai,
        target="channel",
        channel_title="ПП Рецепты",
    )
    await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "image_prompt",
                    "enabled": True,
                    "config": {"mode": "from_post", "instruction": "Сгенерируй картинку для этого поста"},
                },
                {"id": "2", "type": "image_gen", "enabled": True, "config": {}},
                {
                    "id": "3",
                    "type": "post_gen",
                    "enabled": True,
                    "config": {
                        "mode": "ai",
                        "user_input": "Каждый пост — ПП-рецепт с КБЖУ",
                        "generated_post": "СТАРОЕ ПРЕВЬЮ НЕ БРАТЬ",
                        "bold_headings": True,
                        "use_emoji": False,
                        "comments_enabled": False,
                        "add_channel_link": False,
                    },
                },
            ],
            "schedule": {"enabled": False, "frequency": "daily", "times": []},
        },
    )
    assert len(oai.calls) >= 3
    propose_prompt = oai.calls[0]["prompt"]
    write_prompt = oai.calls[-1]["prompt"]
    assert "Гречка с индейкой" in propose_prompt
    assert "Салат с тунцом" in propose_prompt
    assert "НЕ повторяй" in propose_prompt
    assert "без эмодзи" in write_prompt.lower() or "Не используй эмодзи" in write_prompt
    assert "**текст**" in write_prompt or "жирным" in write_prompt.lower()
    assert "комментарии" in write_prompt.lower()
    assert "реакци" in write_prompt.lower()
    assert "поделитесь с друзьями" in write_prompt.lower()
    assert "СТАРОЕ ПРЕВЬЮ" not in ctx.post_text
    assert "Салат с киноа" in ctx.post_text
    assert len(sent) == 1
    assert "Салат с киноа" in sent[0]["text"]


@pytest.mark.asyncio
async def test_image_prompt_appends_visual_style():
    class _Style:
        visual_style = "яркие фото блюд сверху, натуральный свет, белый фон"

    class _Channel:
        style_profile = _Style()

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="https://max.ru/pp_recipes",
        run_id=1,
        max_client=None,
        openai_client=None,
        target="channel",
        post_text="Салат с киноа",
    )
    await ImagePromptBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "from_post",
            "instruction": "Сгенерируй картинку для этого поста",
        },
    )
    assert "Салат с киноа" in ctx.image_prompt
    assert "яркие фото блюд сверху" in ctx.image_prompt
    assert "Визуальный стиль канала" in ctx.image_prompt


@pytest.mark.asyncio
async def test_image_prompt_fixed_skips_visual_style_by_default():
    class _Style:
        visual_style = "Ты создаёшь ежедневные идеи для открыток"

    class _Channel:
        style_profile = _Style()
        max_chat_id = 99

    class _Max:
        async def get_messages(self, chat_id, count=50):
            return [{"body": {"text": "🐶 Собака на закате\nтекст"}}]

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="https://max.ru/channel_otkritki_ot_dushi",
        run_id=4,
        max_client=_Max(),
        openai_client=None,
        target="channel",
    )
    await ImagePromptBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "fixed",
            "generated_prompt": "Создай оригинальную премиальную открытку",
        },
    )
    assert "Создай оригинальную премиальную открытку" in ctx.image_prompt
    assert "Визуальный стиль канала" not in ctx.image_prompt
    assert "Ты создаёшь ежедневные идеи" not in ctx.image_prompt
    assert "Собака на закате" in ctx.image_prompt
    assert "НЕ повторяй" in ctx.image_prompt


@pytest.mark.asyncio
async def test_image_gen_keeps_uploads_clean_without_channel_link_arg():
    calls: list[dict] = []

    class _OAI:
        async def generate_image(self, prompt: str):
            calls.append({"prompt": prompt})
            return "/tmp/img.png"

    ctx = PipelineContext(
        channel=object(),  # type: ignore[arg-type]
        channel_link="https://max.ru/pp_recipes",
        run_id=1,
        max_client=None,
        openai_client=_OAI(),
        target="channel",
        image_prompt="food photo",
    )
    await ImageGenBlock().execute(ctx, {"add_watermark": True})
    assert calls == [{"prompt": "food photo"}]
    assert ctx.image_url == "/tmp/img.png"


@pytest.mark.asyncio
async def test_image_gen_defaults_generate_without_extra_args():
    calls: list[dict] = []

    class _OAI:
        async def generate_image(self, prompt: str):
            calls.append({"prompt": prompt})
            return "/tmp/img.png"

    ctx = PipelineContext(
        channel=object(),  # type: ignore[arg-type]
        channel_link="https://max.ru/pp_recipes",
        run_id=4,
        max_client=None,
        openai_client=_OAI(),
        target="channel",
        image_prompt="food photo",
    )
    await ImageGenBlock().execute(ctx, {})
    assert calls == [{"prompt": "food photo"}]


@pytest.mark.asyncio
async def test_image_gen_appends_no_text_when_disallowed():
    calls: list[dict] = []

    class _OAI:
        async def generate_image(self, prompt: str):
            calls.append({"prompt": prompt})
            return "/tmp/img.png"

    ctx = PipelineContext(
        channel=object(),  # type: ignore[arg-type]
        channel_link="https://max.ru/pp_recipes",
        run_id=3,
        max_client=None,
        openai_client=_OAI(),
        target="channel",
        image_prompt="Букет пионов на столе",
    )
    await ImageGenBlock().execute(
        ctx,
        {"add_watermark": True, "allow_text": False},
    )
    assert len(calls) == 1
    assert "Букет пионов на столе" in calls[0]["prompt"]
    assert "Без текста" in calls[0]["prompt"]


@pytest.mark.asyncio
async def test_image_prompt_from_news_uses_source_image():
    ctx = PipelineContext(
        channel=object(),  # type: ignore[arg-type]
        channel_link="https://max.ru/ekb",
        run_id=9,
        max_client=None,
        openai_client=None,
        target="channel",
        meta={
            "news_item": {
                "title": "Парк открыли",
                "summary": "В городе открыли парк",
                "image_url": "https://cdn.example.com/park.jpg",
            }
        },
    )
    await ImagePromptBlock().execute(ctx, {"enabled": True, "mode": "from_news"})
    assert ctx.meta["image_source"] == "news"
    assert ctx.image_prompt == ""


@pytest.mark.asyncio
async def test_image_prompt_from_news_ai_fallback_without_image():
    ctx = PipelineContext(
        channel=object(),  # type: ignore[arg-type]
        channel_link="https://max.ru/ekb",
        run_id=10,
        max_client=None,
        openai_client=None,
        target="channel",
        meta={
            "news_item": {
                "title": "Парк открыли",
                "summary": "В городе открыли парк у реки",
                "image_url": None,
            }
        },
    )
    await ImagePromptBlock().execute(ctx, {"enabled": True, "mode": "from_news"})
    assert ctx.meta["image_source"] == "ai"
    assert "Парк открыли" in ctx.image_prompt
    assert "у реки" in ctx.image_prompt


@pytest.mark.asyncio
async def test_image_gen_uses_news_url_without_openai():
    class _OAI:
        async def generate_image(self, prompt: str):
            raise AssertionError("should not generate")

    ctx = PipelineContext(
        channel=object(),  # type: ignore[arg-type]
        channel_link="https://max.ru/ekb",
        run_id=11,
        max_client=None,
        openai_client=_OAI(),
        target="channel",
        meta={
            "image_source": "news",
            "news_item": {"image_url": "https://cdn.example.com/park.jpg"},
        },
    )
    await ImageGenBlock().execute(ctx, {})
    assert ctx.image_url == "https://cdn.example.com/park.jpg"


@pytest.mark.asyncio
async def test_runner_sets_add_watermark_meta_and_keeps_image_clean():
    calls: list[dict] = []

    class _OAI:
        async def generate_image(self, prompt: str):
            calls.append({"prompt": prompt})
            return "/tmp/img.png"

    class _Max:
        async def get_messages(self, chat_id, count=50):
            return []

    class _Channel:
        max_chat_id = 1

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="https://max.ru/channel_otkritki_ot_dushi",
        run_id=4,
        max_client=_Max(),
        openai_client=_OAI(),
        target="channel",
    )
    await PipelineRunner().run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "image_prompt",
                    "enabled": True,
                    "config": {"mode": "fixed", "generated_prompt": "open card"},
                },
                {"id": "2", "type": "image_gen", "enabled": True, "config": {"add_watermark": True}},
                {
                    "id": "3",
                    "type": "video_gen",
                    "enabled": True,
                    "config": {"generated_prompt": ""},  # no video prompt → skip video body
                },
            ],
            "schedule": {"enabled": False, "frequency": "daily", "times": []},
        },
    )
    assert ctx.meta.get("add_watermark") is True
    assert "skip_image_watermark" not in ctx.meta
    assert len(calls) == 1
    assert ctx.image_url == "/tmp/img.png"


@pytest.mark.asyncio
async def test_post_gen_attaches_image_when_no_video():
    sent: list[dict] = []

    class _Max:
        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append({"chat_id": chat_id, "text": text, "attachments": attachments})

        async def send_message_to_user(self, user_id, text, attachments=None, fmt=None):
            sent.append({"user_id": user_id, "text": text, "attachments": attachments})

    class _Channel:
        max_chat_id = 42

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        image_url="https://example.com/img.png",
    )
    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "generated_post": "Готовый пост",
            "add_channel_link": False,
        },
    )
    assert len(sent) == 1
    assert sent[0]["attachments"] == [
        {"type": "image", "payload": {"url": "https://example.com/img.png"}}
    ]


@pytest.mark.asyncio
async def test_post_gen_prefers_video_over_image():
    sent: list[dict] = []

    class _Max:
        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append({"attachments": attachments})

    class _Channel:
        max_chat_id = 42

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        image_url="https://example.com/img.png",
        video_token="vid-token",
    )
    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "generated_post": "Открытка",
            "add_channel_link": False,
        },
    )
    assert sent[0]["attachments"] == [
        {"type": "video", "payload": {"token": "vid-token"}}
    ]


@pytest.mark.asyncio
async def test_post_gen_keeps_local_image_after_upload(tmp_path):
    local = tmp_path / "logo_abc.png"
    local.write_bytes(b"fake-png")
    uploaded: list[tuple[str, str]] = []

    class _Max:
        async def upload_file(self, path, kind):
            uploaded.append((path, kind))
            assert Path(path).exists()
            return "img-token"

        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            return None

    class _Channel:
        max_chat_id = 42

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        image_url=str(local),
    )
    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "generated_post": "Пост с картинкой",
            "add_channel_link": False,
        },
    )
    assert uploaded == [(str(local), "image")]
    assert local.exists()
    assert ctx.image_url == str(local)


@pytest.mark.asyncio
async def test_post_gen_keeps_remote_image_url():
    class _Max:
        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            return None

    class _Channel:
        max_chat_id = 42

    remote = "https://cdn.example.com/news.jpg"
    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        image_url=remote,
    )
    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "generated_post": "Новость",
            "add_channel_link": False,
        },
    )
    assert ctx.image_url == remote


@pytest.mark.asyncio
async def test_post_gen_uploads_audio_and_keeps_local_file(tmp_path):
    local = tmp_path / "tts_story.mp3"
    local.write_bytes(b"fake-mp3")
    uploaded: list[tuple[str, str]] = []
    sent: list[dict] = []

    class _Max:
        async def upload_file(self, path, kind):
            uploaded.append((path, kind))
            assert Path(path).exists()
            return "audio-token"

        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append({"text": text, "attachments": attachments})

    class _Channel:
        max_chat_id = 42

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        audio_local_path=str(local),
        post_text="🎧 Аудиосказка",
    )
    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "generated_post": "🎧 Аудиосказка",
            "add_channel_link": False,
        },
    )
    assert uploaded == [(str(local), "audio")]
    assert sent[0]["attachments"] == [
        {"type": "audio", "payload": {"token": "audio-token"}}
    ]
    assert "Поделитесь с друзьями — пусть и у них будет добрая сказка перед сном" in sent[0]["text"]
    assert local.exists()
    assert ctx.audio_local_path == str(local)


@pytest.mark.asyncio
async def test_post_gen_sends_image_then_audio_as_two_messages(tmp_path):
    local_audio = tmp_path / "story.mp3"
    local_audio.write_bytes(b"fake-mp3")
    local_image = tmp_path / "cover.png"
    local_image.write_bytes(b"fake-png")
    sent: list[dict] = []

    class _Max:
        async def upload_file(self, path, kind):
            return f"{kind}-token"

        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append({"text": text, "attachments": attachments})

    class _Channel:
        max_chat_id = 7
        telegram_chat_id = None
        telegram_link = None

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        image_url=str(local_image),
        audio_local_path=str(local_audio),
        post_text="Маленький ёжик",
    )
    await PostGenBlock().execute(
        ctx,
        {"enabled": True, "add_channel_link": False},
    )
    assert len(sent) == 2
    assert sent[0]["attachments"] == [
        {"type": "image", "payload": {"token": "image-token"}}
    ]
    assert "ёжик" in sent[0]["text"]
    assert sent[1]["attachments"] == [
        {"type": "audio", "payload": {"token": "audio-token"}}
    ]
    assert local_audio.exists()
    assert local_image.exists()


@pytest.mark.asyncio
async def test_tts_gen_writes_audio_path():
    from app.application.pipeline.blocks.tts_gen import TtsGenBlock

    class _OAI:
        async def generate_speech(self, text, **kwargs):
            assert "сказка" in text
            assert kwargs["voice"] == "shimmer"
            assert kwargs["speed"] == 0.85
            assert kwargs["model"] == "gpt-4o-mini-tts"
            assert kwargs["instructions"]
            assert "bedtime" in kwargs["instructions"].lower() or "softly" in kwargs["instructions"].lower()
            return "/tmp/out.mp3"

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=_OAI(),
        story_script="Длинная сказка про лес",
    )
    await TtsGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "provider": "openai",
            "model": "gpt-4o-mini-tts",
            "voice": "shimmer",
            "speed": 0.85,
            "instructions": (
                "Speak softly and calmly, like a bedtime storyteller for a young child."
            ),
        },
    )
    assert ctx.audio_local_path == "/tmp/out.mp3"


@pytest.mark.asyncio
async def test_story_gen_sets_caption_and_script(monkeypatch):
    from app.application.pipeline.blocks.story_gen import StoryGenBlock
    from app.application.pipeline.tale_video import TaleScene, TaleScript

    async def _fake_script(*, topic, **kwargs):
        return TaleScript(
            title="Тим",
            caption="🌙 Сказка про Тима",
            story="Жил-был ёжик Тим. " * 20,
            scenes=[
                TaleScene(id=i, story_span=f"s{i}", image_prompt_en="p")
                for i in range(1, 7)
            ],
        )

    monkeypatch.setattr(
        "app.application.pipeline.tale_video.generate_tale_script",
        _fake_script,
    )

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=None,
        target="user",
        channel_title="Аудиосказки",
    )
    await StoryGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "ai",
            "user_input": "добрые сказки",
            "target_minutes": 5,
            "format": "fairy_tale",
        },
    )
    assert ctx.post_text.startswith("🌙")
    assert "ёжик" in ctx.story_script
    assert ctx.meta.get("tale_script")


@pytest.mark.asyncio
async def test_story_gen_recovers_from_broken_json_without_leak():
    from app.application.pipeline.blocks.story_gen import (
        _clean_caption,
        _clean_story,
        _extract_json_object,
    )

    broken = (
        '{"caption" : "🌙 Ёжик и луна", "story": "Жил-был маленький ёжик Тим. '
        "Он шёл по тропинке и нашёл друга."
        # missing closing quote/brace — simulates truncated model output
    )
    data = _extract_json_object(broken) or {}
    story = _clean_story(str(data.get("story") or ""), broken)
    caption = _clean_caption(str(data.get("caption") or ""), story)
    assert not caption.startswith("{")
    assert "caption" not in caption[:20]
    assert "ёжик" in story.lower() or "Ёжик" in (caption + story)


def test_build_share_cta_audio():
    from app.application.pipeline.blocks.post_gen import build_share_cta_audio

    text = build_share_cta_audio("🌙 Сказка про луну")
    assert "Поделитесь с друзьями — пусть и у них будет добрая сказка перед сном" in text
    # idempotent
    assert build_share_cta_audio(text) == text


def test_migrate_story_topic_queue_into_post_gen():
    from app.application.pipeline.normalize import normalize_blocks_config

    v2 = normalize_blocks_config(
        {
            "version": 2,
            "steps": [
                {
                    "id": "s",
                    "type": "story_gen",
                    "enabled": True,
                    "config": {"topic_queue": ["Тема А", "Тема Б"], "target_minutes": 5},
                },
                {
                    "id": "p",
                    "type": "post_gen",
                    "enabled": False,
                    "config": {"topic_queue": []},
                },
            ],
            "schedule": {"enabled": False, "frequency": "daily", "times": []},
        }
    )
    story = next(s for s in v2["steps"] if s["type"] == "story_gen")
    post = next(s for s in v2["steps"] if s["type"] == "post_gen")
    assert post["config"]["topic_queue"] == ["Тема А", "Тема Б"]
    assert story["config"]["topic_queue"] == []


@pytest.mark.asyncio
async def test_story_gen_pops_shared_post_topic_queue():
    from app.application.pipeline.blocks.story_gen import StoryGenBlock
    import json

    class _OAI:
        async def generate_text(self, prompt, system_prompt=None):
            assert "Ёжик" in prompt
            return json.dumps(
                {"caption": "Анонс", "story": "Длинная сказка про лес."},
                ensure_ascii=False,
            )

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=_OAI(),
        target="channel",
        channel_title="Аудиосказки",
        meta={"shared_topic_queue": ["Ёжик и луна", "Ещё"]},
    )
    await StoryGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "mode": "ai",
            "user_input": "добрые сказки",
            "target_minutes": 5,
        },
    )
    assert ctx.meta["topic_queue_block"] == "post_gen"
    assert ctx.meta["topic_queue_remaining"] == ["Ещё"]
    assert ctx.meta["shared_topic_queue"] == ["Ещё"]
    assert ctx.post_text == "Анонс"
    from app.application.pipeline.tts_chunking import chunk_tts_text, max_chars_for_model

    assert max_chars_for_model("gpt-4o-mini-tts") == 1600
    assert max_chars_for_model("tts-1-hd") == 4096

    part = "А" * 900
    text = f"{part}.\n\n{part}.\n\n{part}."
    chunks = chunk_tts_text(text, max_chars=1600)
    assert len(chunks) >= 2
    assert all(len(c) <= 1600 for c in chunks)


@pytest.mark.asyncio
async def test_runner_skips_post_preseed_when_story_gen_enabled(monkeypatch):
    from unittest.mock import AsyncMock

    from app.application.auth import feature_access as fa
    from app.config import settings

    monkeypatch.setattr(settings.features, "audio_whitelist", "10")
    fa._audio_whitelist.cache_clear()

    class _Story:
        type_id = "story_gen"

        async def execute(self, ctx, config):
            if config.get("enabled"):
                ctx.story_script = "story body"
                ctx.post_text = "caption"

    class _Post:
        type_id = "post_gen"

        async def execute(self, ctx, config):
            pass

    registry = BlockRegistry()
    registry.register(_Story())
    registry.register(_Post())
    oai = AsyncMock()
    oai.generate_text = AsyncMock(side_effect=AssertionError("should not preseed"))

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=oai,
        target="user",
        meta={"owner_max_user_id": 10},
    )
    config = {
        "version": 2,
        "steps": [
            {"id": "s", "type": "story_gen", "enabled": True, "config": {"mode": "ai"}},
            {"id": "p", "type": "post_gen", "enabled": True, "config": {}},
        ],
        "schedule": {"enabled": False, "frequency": "daily", "times": []},
    }
    await PipelineRunner(registry).run(ctx, config)
    assert ctx.story_script == "story body"
    assert ctx.post_text == "caption"
    oai.generate_text.assert_not_called()



@pytest.mark.asyncio
async def test_post_gen_applies_logo_watermark_on_local_image(tmp_path):
    from PIL import Image

    src = tmp_path / "clean.png"
    logo = tmp_path / "logo.png"
    Image.new("RGB", (200, 200), color=(10, 20, 30)).save(src)
    Image.new("RGBA", (40, 40), color=(255, 0, 0, 255)).save(logo)

    uploaded: list[str] = []
    sent: list[dict] = []

    class _Max:
        async def upload_file(self, path, kind):
            uploaded.append(path)
            assert Path(path).exists()
            # Source in uploads/tmp must stay clean — dest is a temp wm file.
            assert Path(path).resolve() != src.resolve()
            return "wm-token"

        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append({"attachments": attachments})

    class _Channel:
        max_chat_id = 7
        logo_path = str(logo)

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        image_url=str(src),
        meta={"add_watermark": True},
    )
    await PostGenBlock().execute(
        ctx,
        {"enabled": True, "generated_post": "Пост", "add_channel_link": False},
    )
    assert Path(src).read_bytes()  # still exists
    assert uploaded
    assert sent[0]["attachments"] == [
        {"type": "image", "payload": {"token": "wm-token"}}
    ]


@pytest.mark.asyncio
async def test_post_gen_skips_watermark_without_logo(tmp_path):
    src = tmp_path / "clean.png"
    src.write_bytes(b"png")
    uploaded: list[str] = []

    class _Max:
        async def upload_file(self, path, kind):
            uploaded.append(path)
            return "clean-token"

        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            return None

    class _Channel:
        max_chat_id = 7
        logo_path = None

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        image_url=str(src),
        meta={"add_watermark": True},
    )
    await PostGenBlock().execute(
        ctx,
        {"enabled": True, "generated_post": "Пост", "add_channel_link": False},
    )
    assert uploaded == [str(src)]


def test_build_related_channels_footer_format():
    footer = build_related_channels_footer(
        [
            {"title": "Bio [demo]", "link": "https://max.ru/bio"},
            {"title": "Yoga", "link": "https://max.ru/yoga"},
        ]
    )
    assert footer.startswith(f"\n\n{RELATED_CHANNELS_INTRO}\n\n")
    assert "[Bio \\[demo\\]](https://max.ru/bio)" in footer
    assert "[Yoga](https://max.ru/yoga)" in footer
    assert footer.count("\n") >= 3


def test_build_related_channels_footer_empty():
    assert build_related_channels_footer([]) == ""
    assert build_related_channels_footer([{"title": "", "link": "https://x"}]) == ""


def test_normalize_related_channels_dedupe_and_limit():
    raw = [
        {"title": "A", "link": "https://max.ru/a", "source": "manual"},
        {"title": "B", "link": "https://max.ru/a/", "source": "manual"},
        {"title": "C", "link": "https://max.ru/c", "source": "manual"},
        {"title": "D", "link": "https://max.ru/d", "source": "manual"},
        {"title": "E", "link": "https://max.ru/e", "source": "manual"},
        {"title": "F", "link": "https://max.ru/f", "source": "manual"},
        {"title": "G", "link": "https://max.ru/g", "source": "manual"},
        {"title": "H", "link": "https://max.ru/h", "source": "manual"},
    ]
    out = normalize_related_channels(raw)
    assert len(out) == 7
    assert out[0]["title"] == "A"
    assert all(item["link"].startswith("http") for item in out)


def test_normalize_related_channels_connected_without_link():
    out = normalize_related_channels(
        [
            {
                "title": "My Channel",
                "link": "",
                "source": "connected",
                "channel_id": 3,
            }
        ]
    )
    assert len(out) == 1
    assert out[0]["channel_id"] == 3
    assert out[0]["link"] == ""


@pytest.mark.asyncio
async def test_post_gen_related_before_subscribe():
    sent: list[dict] = []

    class _Max:
        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append({"text": text})

    class _Channel:
        max_chat_id = 42

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="https://max.ru/current",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        channel_title="Current",
        post_text="Основной текст",
    )
    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "generated_post": "Основной текст",
            "add_channel_link": True,
            "related_channels_enabled": True,
            "related_channels": [
                {"title": "Bio", "link": "https://max.ru/bio", "source": "manual"},
            ],
        },
    )
    text = sent[0]["text"]
    assert text.index("Основной текст") < text.index(RELATED_CHANNELS_INTRO)
    assert text.index(RELATED_CHANNELS_INTRO) < text.index("Подпишись")
    assert "[Bio](https://max.ru/bio)" in text


@pytest.mark.asyncio
async def test_post_gen_related_only_no_subscribe():
    sent: list[dict] = []

    class _Max:
        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append({"text": text})

    class _Channel:
        max_chat_id = 42

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        post_text="Только related",
    )
    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "generated_post": "Только related",
            "add_channel_link": False,
            "related_channels_enabled": True,
            "related_channels": [
                {"title": "Bio", "link": "https://max.ru/bio", "source": "manual"},
            ],
        },
    )
    text = sent[0]["text"]
    assert RELATED_CHANNELS_INTRO in text
    assert "Подпишись" not in text


@pytest.mark.asyncio
async def test_post_gen_subscribe_only_no_related():
    sent: list[dict] = []

    class _Max:
        async def send_message(self, chat_id, text, attachments=None, fmt=None):
            sent.append({"text": text})

    class _Channel:
        max_chat_id = 42

    ctx = PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="https://max.ru/current",
        run_id=1,
        max_client=_Max(),
        openai_client=None,
        target="channel",
        channel_title="Current",
        post_text="Без related",
    )
    await PostGenBlock().execute(
        ctx,
        {
            "enabled": True,
            "generated_post": "Без related",
            "add_channel_link": True,
            "related_channels_enabled": False,
        },
    )
    text = sent[0]["text"]
    assert RELATED_CHANNELS_INTRO not in text
    assert "Подпишись" in text
    assert build_subscribe_cta("https://max.ru/current", title="Current") in text
