import pytest

from app.application.pipeline.normalize import (
    normalize_blocks_config,
    steps_to_ui_dict,
    ui_dict_to_v2,
    is_v2,
)
from app.application.pipeline.runner import PipelineRunner
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.blocks.registry import BlockRegistry
from app.application.pipeline.blocks.image_prompt import ImagePromptBlock
from app.application.pipeline.blocks.image_gen import ImageGenBlock
from app.application.pipeline.blocks.post_gen import PostGenBlock
from app.bot.states.ai_studio import DEFAULT_BLOCKS


def test_normalize_legacy_dict_to_v2():
    v2 = normalize_blocks_config(DEFAULT_BLOCKS)
    assert v2["version"] == 2
    assert [s["type"] for s in v2["steps"]] == [
        "image_prompt",
        "image_gen",
        "video_gen",
        "post_gen",
    ]
    assert "schedule" in v2
    assert v2["schedule"]["enabled"] is False
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
        "image_gen": {"enabled": True, "model": "gpt-image-2"},
        "video_gen": {
            "enabled": False,
            "model": "seedance-1.5-pro",
            "duration": 4,
            "mode": "normal",
            "resolution": "480p",
            "aspect_ratio": "9:16",
            "fixed_lens": False,
            "generate_audio": False,
            "fallback_model": "wan2.5-image-to-video",
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
        "schedule": {"enabled": True, "frequency": "daily", "times": ["10:00"]},
    }
    v2 = ui_dict_to_v2(ui)
    back = steps_to_ui_dict(v2)
    assert back["image_prompt"]["mode"] == "from_post"
    assert back["image_prompt"]["instruction"] == "Сгенерируй картинку для этого поста"
    assert back["post_gen"]["user_input"] == "бриф рецептов"
    assert back["post_gen"]["bold_headings"] is False
    assert back["post_gen"]["use_emoji"] is True
    assert back["post_gen"]["comments_enabled"] is False
    assert back["schedule"]["times"] == ["10:00"]
    assert back["image_gen"]["model"] == "gpt-image-2"
    assert back["video_gen"]["model"] == "seedance-1.5-pro"
    assert back["video_gen"]["duration"] == 4
    assert back["video_gen"]["aspect_ratio"] == "9:16"
    assert back["video_gen"]["fallback_model"] == "wan2.5-image-to-video"
    assert back["video_gen"]["generate_audio"] is False


@pytest.mark.asyncio
async def test_video_gen_calls_generate_with_fallback():
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
    ):
        await VideoGenBlock().execute(
            ctx,
            {
                "enabled": True,
                "model": "seedance-1.5-pro",
                "generated_prompt": "slow zoom",
                "fallback_model": "wan2.5-image-to-video",
            },
        )

    assert captured["kwargs"]["prompt"] == "slow zoom"
    assert captured["kwargs"]["image_url"] == "https://cdn/img.png"
    assert captured["kwargs"]["config"]["model"] == "seedance-1.5-pro"
    assert ctx.video_token == "max-video-token"
    assert captured.get("closed") is True


@pytest.mark.asyncio
async def test_runner_skips_unknown_and_runs_enabled_order():
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
    assert len(oai.calls) == 1
    assert "без эмодзи" in oai.calls[0]["prompt"].lower() or "Не используй эмодзи" in oai.calls[0]["prompt"]
    assert "**текст**" in oai.calls[0]["prompt"] or "жирным" in oai.calls[0]["prompt"].lower()
    assert "комментарии" in oai.calls[0]["prompt"].lower()
    assert "реакци" in oai.calls[0]["prompt"].lower()
    assert "поделитесь с друзьями" in oai.calls[0]["prompt"].lower()
    assert "Гречка с индейкой" in oai.calls[0]["prompt"]
    assert "Салат с тунцом" in oai.calls[0]["prompt"]
    assert "НЕ повторяй" in oai.calls[0]["prompt"]
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
async def test_image_gen_passes_channel_link_for_watermark():
    calls: list[dict] = []

    class _OAI:
        async def generate_image(self, prompt: str, channel_link: str | None = None):
            calls.append({"prompt": prompt, "channel_link": channel_link})
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
    await ImageGenBlock().execute(ctx, {})
    assert calls == [{"prompt": "food photo", "channel_link": "https://max.ru/pp_recipes"}]
    assert ctx.image_url == "/tmp/img.png"


@pytest.mark.asyncio
async def test_image_gen_skips_watermark_when_video_follows():
    calls: list[dict] = []

    class _OAI:
        async def generate_image(self, prompt: str, channel_link: str | None = None):
            calls.append({"channel_link": channel_link})
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
                {"id": "2", "type": "image_gen", "enabled": True, "config": {}},
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
    assert ctx.meta.get("skip_image_watermark") is True
    assert calls == [{"channel_link": None}]
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
