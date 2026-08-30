from app.application.pipeline.drive_monitor import (
    apply_drive_video_patch,
    is_drive_trigger,
    normalize_drive_video,
)
from app.infrastructure.services.google_drive_client import parse_folder_id


def test_parse_folder_id():
    assert parse_folder_id("abc123XYZ-_") == "abc123XYZ-_"
    url = "https://drive.google.com/drive/folders/1AbC-dEfGhIjK"
    assert parse_folder_id(url) == "1AbC-dEfGhIjK"
    assert parse_folder_id("") == ""


def test_normalize_drive_video_defaults():
    n = normalize_drive_video({})
    assert n["enabled"] is False
    assert n["folder_id"] == ""
    assert n["fixed_caption"] == ""
    assert n["low_stock_threshold"] == 5
    assert n["low_stock_notified_at_remaining"] is None
    assert n["delete_after_publish"] is True


def test_normalize_drive_video_delete_toggle():
    n = normalize_drive_video({"delete_after_publish": False})
    assert n["delete_after_publish"] is False


def test_normalize_drive_video_folder_from_url():
    n = normalize_drive_video(
        {
            "enabled": True,
            "folder_id": "https://drive.google.com/drive/folders/folder123",
            "fixed_caption": "Hello",
            "low_stock_threshold": 3,
        }
    )
    assert n["enabled"] is True
    assert n["folder_id"] == "folder123"
    assert n["fixed_caption"] == "Hello"
    assert n["low_stock_threshold"] == 3


def test_is_drive_trigger():
    assert is_drive_trigger({}) is False
    assert is_drive_trigger({"drive_video": {"enabled": True, "folder_id": ""}}) is False
    assert is_drive_trigger({"drive_video": {"enabled": True, "folder_id": "abc"}}) is True


def test_apply_drive_video_patch_v2():
    cfg = {
        "version": 2,
        "steps": [
            {
                "id": "1",
                "type": "drive_video",
                "enabled": True,
                "config": {"folder_id": "old", "fixed_caption": "a"},
            }
        ],
        "drive_video": {"enabled": True, "folder_id": "old", "fixed_caption": "a"},
    }
    out = apply_drive_video_patch(cfg, {"low_stock_notified_at_remaining": 5})
    assert out["drive_video"]["low_stock_notified_at_remaining"] == 5
    step = out["steps"][0]
    assert step["config"]["low_stock_notified_at_remaining"] == 5
