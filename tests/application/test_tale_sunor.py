"""Tests for Sunor fairy-tale video path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.application.pipeline.tale_prompts import (
    STORY_TARGET_CHARS,
    build_sunor_tags,
    wrap_story_for_sunor,
)
from app.application.pipeline.tale_video import (
    TaleScript,
    TaleScene,
    apply_story_length_limit,
    parse_tale_script,
    scenes_from_story,
    truncate_story,
)
from app.infrastructure.services.sunor_client import SUNOR_MODEL, build_music_input


def test_truncate_story_hard_cap():
    long = ("Жили-были. " * 500).strip()
    assert len(long) > STORY_TARGET_CHARS
    out = truncate_story(long, STORY_TARGET_CHARS)
    assert len(out) <= STORY_TARGET_CHARS
    assert out.endswith(".") or " " not in out[-5:]


def test_apply_story_length_limit_rebuilds_scenes():
    story = ("Слово " * 900).strip()
    assert len(story) > STORY_TARGET_CHARS
    script = TaleScript(
        title="Т",
        caption="К",
        story=story,
        scenes=[
            TaleScene(id=i, story_span=f"span {i}", image_prompt_en="p")
            for i in range(1, 7)
        ],
    )
    limited = apply_story_length_limit(script)
    assert len(limited.story) <= STORY_TARGET_CHARS
    assert len(limited.scenes) >= 6


def test_parse_tale_script_and_scenes():
    raw = (
        '{"title":"Луна","caption":"Добрая сказка.",'
        '"story":"Жил кот. Он спал. Потом утро.",'
        '"scenes":['
        + ",".join(
            f'{{"id":{i},"story_span":"часть {i}","image_prompt_en":"cat {i}"}}'
            for i in range(1, 7)
        )
        + "]}"
    )
    script = parse_tale_script(raw)
    assert script.title == "Луна"
    assert len(script.scenes) == 6
    assert "кот" in script.story.lower() or "Жил" in script.story


def test_scenes_from_story_fallback():
    story = "\n\n".join(f"Абзац номер {i}." for i in range(8))
    scenes = scenes_from_story(story, n=6)
    assert len(scenes) == 6


def test_sunor_tags_fixed_scenario():
    tags = build_sunor_tags()
    assert "children's bedtime story" in tags
    assert "spoken narration only" in tags
    assert "magical forest" in tags.lower()
    assert "children aged 3-6" in tags.lower()


def test_sunor_model_constant_is_suno():
    assert SUNOR_MODEL == "suno"
    inp = build_music_input(
        prompt=wrap_story_for_sunor("Тест"),
        instrumental=False,
        custom_mode=True,
        style=build_sunor_tags(),
        title="Сказка",
        negative_tags="singing",
    )
    assert inp["prompt"].startswith("[Children's bedtime story")
    assert "tags" in inp
    assert inp["title"] == "Сказка"


@pytest.mark.asyncio
async def test_tts_gen_sunor_sets_video_path(monkeypatch, tmp_path):
    from app.application.pipeline.blocks.tts_gen import TtsGenBlock
    from app.application.pipeline.context import PipelineContext
    from app.application.pipeline.tale_video import TaleVideoResult

    video = tmp_path / "tale.mp4"
    video.write_bytes(b"fake")
    audio = tmp_path / "tale.mp3"
    audio.write_bytes(b"fake")

    async def _fake_build(script):
        return TaleVideoResult(
            title=script.title,
            caption=script.caption,
            story=script.story,
            video_path=str(video),
            audio_path=str(audio),
            sunor_task_id="task-1",
            scene_count=len(script.scenes),
        )

    monkeypatch.setattr(
        "app.application.pipeline.tale_video.build_tale_video_from_script",
        _fake_build,
    )

    script = TaleScript(
        title="Т",
        caption="Капшн",
        story="Жил-был зайчик.",
        scenes=[
            TaleScene(id=i, story_span=f"s{i}", image_prompt_en="p")
            for i in range(1, 7)
        ],
    )
    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=None,
        story_script=script.story,
        post_text=script.caption,
        meta={"tale_script": script.to_meta()},
    )
    await TtsGenBlock().execute(ctx, {"enabled": True, "provider": "sunor"})
    assert ctx.video_local_path == str(video)
    assert ctx.audio_local_path == str(audio)
    assert ctx.meta["tale_sunor_task_id"] == "task-1"


@pytest.mark.asyncio
async def test_runner_skips_image_blocks_for_sunor_tale(monkeypatch):
    from app.application.pipeline.context import PipelineContext
    from app.application.pipeline.runner import PipelineRunner

    executed: list[str] = []

    class RecBlock:
        def __init__(self, tid):
            self.type_id = tid

        async def execute(self, ctx, config):
            executed.append(self.type_id)

    class Reg:
        def get(self, tid):
            return RecBlock(tid)

    monkeypatch.setattr(
        "app.application.pipeline.runner.audio_allowed", lambda _uid: True
    )
    monkeypatch.setattr(
        "app.application.pipeline.runner.video_allowed", lambda _uid: True
    )

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=None,
        meta={"owner_max_user_id": 1},
        story_script="x",
        post_text="y",
    )
    blocks = {
        "version": 2,
        "steps": [
            {
                "id": "1",
                "type": "story_gen",
                "enabled": True,
                "config": {"format": "fairy_tale"},
            },
            {"id": "2", "type": "image_prompt", "enabled": True, "config": {}},
            {"id": "3", "type": "image_gen", "enabled": True, "config": {}},
            {"id": "4", "type": "video_gen", "enabled": True, "config": {}},
            {
                "id": "5",
                "type": "tts_gen",
                "enabled": True,
                "config": {"provider": "sunor"},
            },
            {"id": "6", "type": "post_gen", "enabled": True, "config": {}},
        ],
        "schedule": {},
    }
    await PipelineRunner(registry=Reg()).run(ctx, blocks)
    assert "image_prompt" not in executed
    assert "image_gen" not in executed
    assert "video_gen" not in executed
    assert "story_gen" in executed
    assert "tts_gen" in executed
    assert "post_gen" in executed


@pytest.mark.asyncio
async def test_download_url_to_file_retries_after_403(monkeypatch, tmp_path):
    from app.application.pipeline import tale_video as tv

    monkeypatch.setattr(tv, "_cdn_download_proxy", lambda: None)
    monkeypatch.setattr(tv.asyncio, "sleep", AsyncMock())

    calls = {"n": 0}

    class FakeResp:
        def __init__(self, status_code: int, content: bytes = b""):
            self.status_code = status_code
            self.content = content
            self.request = httpx.Request("GET", "https://cdn1.suno.ai/a.mp3")

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {self.status_code}",
                    request=self.request,
                    response=self,
                )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResp(403)
            return FakeResp(200, b"ID3audio")

    monkeypatch.setattr(tv.httpx, "AsyncClient", FakeClient)
    dest = tmp_path / "out.mp3"
    out = await tv.download_url_to_file(
        "https://cdn1.suno.ai/a.mp3",
        dest,
        attempts=3,
    )
    assert out == dest
    assert dest.read_bytes() == b"ID3audio"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_synthesize_falls_back_to_second_track(monkeypatch, tmp_path):
    from app.application.pipeline import tale_video as tv
    from app.infrastructure.services.sunor_client import MusicTrack

    monkeypatch.setattr(tv.settings.sunor, "api_key", "k")
    monkeypatch.setattr(tv, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(
        tv,
        "post_create_music_task",
        AsyncMock(return_value="task-x"),
    )
    monkeypatch.setattr(
        tv,
        "poll_music_task",
        AsyncMock(
            return_value=[
                MusicTrack(audio_id="1", audio_url="https://cdn1.suno.ai/1.mp3"),
                MusicTrack(audio_id="2", audio_url="https://cdn1.suno.ai/2.mp3"),
            ]
        ),
    )

    tried: list[str] = []

    async def _fake_dl(url, dest, **kwargs):
        tried.append(url)
        if "1.mp3" in url:
            req = httpx.Request("GET", url)
            resp = httpx.Response(403, request=req)
            raise httpx.HTTPStatusError("HTTP 403", request=req, response=resp)
        dest.write_bytes(b"ok")
        return dest

    monkeypatch.setattr(tv, "download_url_to_file", _fake_dl)
    path, task_id = await tv.synthesize_tale_audio_sunor(
        story="Жил-был кот.",
        title="Кот",
    )
    assert task_id == "task-x"
    assert Path(path).read_bytes() == b"ok"
    assert tried == [
        "https://cdn1.suno.ai/1.mp3",
        "https://cdn1.suno.ai/2.mp3",
    ]


@pytest.mark.asyncio
async def test_synthesize_final_403_in_error_message(monkeypatch, tmp_path):
    from app.application.pipeline import tale_video as tv
    from app.infrastructure.services.sunor_client import MusicTrack

    monkeypatch.setattr(tv.settings.sunor, "api_key", "k")
    monkeypatch.setattr(tv, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(
        tv,
        "post_create_music_task",
        AsyncMock(return_value="task-y"),
    )
    monkeypatch.setattr(
        tv,
        "poll_music_task",
        AsyncMock(
            return_value=[
                MusicTrack(audio_id="1", audio_url="https://cdn1.suno.ai/1.mp3"),
            ]
        ),
    )

    async def _always_403(url, dest, **kwargs):
        req = httpx.Request("GET", url)
        resp = httpx.Response(403, request=req)
        raise httpx.HTTPStatusError("HTTP 403", request=req, response=resp)

    monkeypatch.setattr(tv, "download_url_to_file", _always_403)
    with pytest.raises(tv.TaleGenerationError) as ei:
        await tv.synthesize_tale_audio_sunor(story="Жил-был кот.", title="Кот")
    assert "403" in str(ei.value)
