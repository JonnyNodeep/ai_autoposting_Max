"""Tests for sunor_gen in pipeline normalize."""

from app.application.pipeline.normalize import (
    STEP_ORDER,
    normalize_blocks_config,
)


def test_step_order_includes_sunor_gen():
    assert "sunor_gen" in STEP_ORDER
    assert STEP_ORDER.index("sunor_gen") > STEP_ORDER.index("tts_gen")
    assert STEP_ORDER.index("post_gen") > STEP_ORDER.index("sunor_gen")


def test_normalize_sunor_gen_config():
    v2 = normalize_blocks_config(
        {
            "sunor_gen": {
                "enabled": True,
                "music_mode": "custom",
                "tags": "lullaby",
                "target_duration_sec": 9999,
                "continue_at_sec": 200,
                "pick_variant": "invalid",
            }
        }
    )
    step = next(s for s in v2["steps"] if s["type"] == "sunor_gen")
    cfg = step["config"]
    assert step["enabled"] is True
    assert cfg["music_mode"] == "custom"
    assert cfg["target_duration_sec"] == 600
    assert cfg["continue_at_sec"] == 120
    assert cfg["pick_variant"] == "first"
