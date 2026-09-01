from pathlib import Path

import pytest

from app.application.pipeline.context import PipelineContext
from app.application.pipeline.upload_cleanup import (
    cleanup_pipeline_uploads,
    is_ephemeral_upload,
    safe_unlink_upload,
)


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    import app.application.pipeline.upload_cleanup as uc

    monkeypatch.setattr(uc, "UPLOAD_DIR", tmp_path)
    return tmp_path


def test_is_ephemeral_upload_accepts_temp_file(upload_root):
    f = upload_root / "video_abc.mp4"
    f.write_bytes(b"x")
    assert is_ephemeral_upload(str(f)) is True


def test_is_ephemeral_upload_rejects_logo(upload_root):
    logos = upload_root / "logos"
    logos.mkdir()
    f = logos / "11.png"
    f.write_bytes(b"x")
    assert is_ephemeral_upload(str(f)) is False


def test_is_ephemeral_upload_rejects_http_url():
    assert is_ephemeral_upload("https://cdn.example.com/a.png") is False


def test_is_ephemeral_upload_rejects_outside_upload_dir(upload_root):
    outside = upload_root.parent / "elsewhere.png"
    outside.write_bytes(b"x")
    assert is_ephemeral_upload(str(outside)) is False


def test_safe_unlink_upload_removes_ephemeral_file(upload_root):
    f = upload_root / "tts_deadbeef.mp3"
    f.write_bytes(b"x")
    safe_unlink_upload(str(f))
    assert not f.exists()


def test_safe_unlink_upload_keeps_logo(upload_root):
    logos = upload_root / "logos"
    logos.mkdir()
    f = logos / "3.png"
    f.write_bytes(b"x")
    safe_unlink_upload(str(f))
    assert f.exists()


def test_cleanup_pipeline_uploads_removes_ctx_paths(upload_root):
    image = upload_root / "logo_temp.png"
    video = upload_root / "video_temp.mp4"
    audio = upload_root / "tts_temp.mp3"
    logo = upload_root / "logos" / "1.png"
    logo.parent.mkdir()
    for p in (image, video, audio, logo):
        p.write_bytes(b"x")

    ctx = PipelineContext(
        channel=None,
        channel_link="",
        run_id=1,
        max_client=None,
        openai_client=None,
        image_url=str(image),
        video_local_path=str(video),
        audio_local_path=str(audio),
    )
    cleanup_pipeline_uploads(ctx)

    assert not image.exists()
    assert not video.exists()
    assert not audio.exists()
    assert logo.exists()
