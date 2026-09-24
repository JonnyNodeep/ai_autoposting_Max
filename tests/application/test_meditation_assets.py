import json
from pathlib import Path

import pytest

from app.application.pipeline.blocks.registry import BlockRegistry
from app.application.pipeline.context import PipelineContext
from app.application.pipeline.meditation_assets import (
    asset_slug,
    persist_meditation_assets,
)
from app.application.pipeline.runner import PipelineRunner


class _Channel:
    id = 12
    max_chat_id = 1
    owner_id = 1


class _IdlePostBlock:
    type_id = "post_gen"

    async def execute(self, ctx, config):
        return


def _ctx(
    *,
    audio: Path | None = None,
    image: str | Path | None = None,
    meditation: bool = True,
    slot_time: str = "04:30",
    topic: str = "Дыхание 4-7-8",
    post_text: str = "caption",
) -> PipelineContext:
    meta: dict = {}
    if meditation:
        meta["pipeline_schedule"] = {"meditation_pipeline": True}
    if slot_time:
        meta["slot_time"] = slot_time
    if topic:
        meta["display_title"] = topic
    return PipelineContext(
        channel=_Channel(),  # type: ignore[arg-type]
        channel_link="",
        run_id=7,
        max_client=None,
        openai_client=None,
        post_text=post_text,
        audio_local_path=str(audio) if audio is not None else "",
        image_url=str(image) if image is not None else "",
        meta=meta,
    )


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    import app.application.pipeline.meditation_assets as ma
    import app.application.pipeline.upload_cleanup as uc

    monkeypatch.setattr(ma, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(uc, "UPLOAD_DIR", tmp_path)
    return tmp_path


def test_asset_slug_from_topic():
    assert asset_slug("Дыхание 4-7-8") == "dyhanie-4-7-8"


@pytest.mark.asyncio
async def test_persist_skips_non_meditation(upload_root):
    audio = upload_root / "track.mp3"
    image = upload_root / "logo_abc.png"
    audio.write_bytes(b"a")
    image.write_bytes(b"i")
    ctx = _ctx(audio=audio, image=image, meditation=False)

    dest = await persist_meditation_assets(ctx)

    assert dest is None
    assert audio.exists()
    assert image.exists()
    assert not (upload_root / "meditations").exists()


@pytest.mark.asyncio
async def test_persist_moves_local_audio_and_image(upload_root):
    audio = upload_root / "sunor_abc.mp3"
    image = upload_root / "logo_abc.png"
    audio.write_bytes(b"audio-bytes")
    image.write_bytes(b"image-bytes")
    ctx = _ctx(audio=audio, image=image)

    dest = await persist_meditation_assets(ctx)

    assert dest is not None
    assert dest.name.startswith("20")
    assert dest.name.endswith("_dyhanie-4-7-8")
    assert "0430" in dest.name
    archived_audio = dest / "dyhanie-4-7-8.mp3"
    archived_image = dest / "cover.png"
    assert archived_audio.is_file()
    assert archived_image.is_file()
    assert archived_audio.read_bytes() == b"audio-bytes"
    assert archived_image.read_bytes() == b"image-bytes"
    assert not audio.exists()
    assert not image.exists()
    assert ctx.audio_local_path == str(archived_audio)
    assert ctx.image_url == str(archived_image)

    meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert meta["channel_id"] == 12
    assert meta["slot_time"] == "04:30"
    assert meta["topic"] == "Дыхание 4-7-8"
    assert meta["run_id"] == 7
    assert meta["audio"].endswith("dyhanie-4-7-8.mp3")
    assert meta["image"].endswith("cover.png")
    assert meta["audio"].startswith("uploads/meditations/12/")
    assert "created_at" in meta


@pytest.mark.asyncio
async def test_persist_downloads_remote_image(upload_root, monkeypatch):
    audio = upload_root / "track.mp3"
    audio.write_bytes(b"a")

    async def fake_download(url, dest, **kwargs):
        Path(dest).write_bytes(b"remote-img")
        return Path(dest)

    monkeypatch.setattr(
        "app.application.pipeline.tale_video.download_url_to_file",
        fake_download,
    )
    ctx = _ctx(audio=audio, image="https://cdn.example.com/cover.jpg")

    dest = await persist_meditation_assets(ctx)

    assert dest is not None
    cover = dest / "cover.jpg"
    assert cover.is_file()
    assert cover.read_bytes() == b"remote-img"
    assert ctx.image_url == str(cover)
    meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert meta["image"].endswith("cover.jpg")


@pytest.mark.asyncio
async def test_runner_persists_meditation_and_skips_cleanup(upload_root):
    audio = upload_root / "track.mp3"
    image = upload_root / "logo_abc.png"
    audio.write_bytes(b"a")
    image.write_bytes(b"i")

    registry = BlockRegistry()
    registry.register(_IdlePostBlock())
    ctx = _ctx(audio=audio, image=image)
    ctx.post_text = "already seeded"

    await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "post_gen",
                    "enabled": True,
                    "config": {"mode": "manual", "generated_post": "already seeded"},
                }
            ],
            "schedule": {"meditation_pipeline": True, "times": ["04:30"]},
        },
    )

    archive_root = upload_root / "meditations" / "12"
    folders = list(archive_root.iterdir()) if archive_root.exists() else []
    assert len(folders) == 1
    dest = folders[0]
    assert (dest / "dyhanie-4-7-8.mp3").is_file()
    assert (dest / "cover.png").is_file()
    assert (dest / "meta.json").is_file()
    assert not audio.exists()
    assert not image.exists()


@pytest.mark.asyncio
async def test_runner_cleanup_still_removes_non_meditation(upload_root):
    audio = upload_root / "tts_temp.mp3"
    image = upload_root / "logo_temp.png"
    audio.write_bytes(b"a")
    image.write_bytes(b"i")

    registry = BlockRegistry()
    registry.register(_IdlePostBlock())
    ctx = _ctx(audio=audio, image=image, meditation=False)
    ctx.post_text = "post"

    await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "post_gen",
                    "enabled": True,
                    "config": {"mode": "manual", "generated_post": "post"},
                }
            ],
            "schedule": {"meditation_pipeline": False},
        },
    )

    assert not audio.exists()
    assert not image.exists()
    assert not (upload_root / "meditations").exists()


@pytest.mark.asyncio
async def test_runner_cleanup_runs_if_persist_fails(upload_root, monkeypatch):
    audio = upload_root / "tts_temp.mp3"
    audio.write_bytes(b"a")

    async def boom(_ctx):
        raise RuntimeError("persist failed")

    monkeypatch.setattr(
        "app.application.pipeline.runner.persist_meditation_assets",
        boom,
    )
    registry = BlockRegistry()
    registry.register(_IdlePostBlock())
    ctx = _ctx(audio=audio, meditation=False)
    ctx.post_text = "post"

    await PipelineRunner(registry).run(
        ctx,
        {
            "version": 2,
            "steps": [
                {
                    "id": "1",
                    "type": "post_gen",
                    "enabled": True,
                    "config": {"mode": "manual", "generated_post": "post"},
                }
            ],
            "schedule": {"meditation_pipeline": False},
        },
    )

    assert not audio.exists()
