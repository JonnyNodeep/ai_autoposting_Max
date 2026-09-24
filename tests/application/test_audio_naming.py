"""Audio filename sanitization from topic titles."""

from pathlib import Path

from app.application.pipeline.audio_naming import rename_audio_path, safe_filename_from_topic


def test_safe_filename_from_topic_translit():
    name = safe_filename_from_topic("Утренняя аффirmация: сила мысли")
    assert name.endswith(".mp3")
    assert " " in name or "-" not in name  # spaces preserved
    assert "Утр" not in name
    assert "Utren" in name or "utren" in name.lower()


def test_safe_filename_empty_topic():
    assert safe_filename_from_topic("") == "audio.mp3"
    assert safe_filename_from_topic("   ") == "audio.mp3"


def test_safe_filename_strips_emoji():
    name = safe_filename_from_topic("🌙 Глубокий сон")
    assert "🌙" not in name
    assert name.endswith(".mp3")


def test_rename_audio_path(tmp_path: Path):
    src = tmp_path / "sunor_abc123.mp3"
    src.write_bytes(b"fake")
    out = rename_audio_path(str(src), "Дыхание 4-7-8")
    assert Path(out).is_file()
    assert Path(out).name.endswith(".mp3")
    assert Path(out).name != "sunor_abc123.mp3"
    assert not src.exists()
