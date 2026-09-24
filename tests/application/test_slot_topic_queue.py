"""Per-slot topic queues for meditation pipeline."""

from app.application.pipeline.meditation_presets import LUNCH_UTC, MORNING_UTC
from app.application.pipeline.normalize import normalize_blocks_config
from app.application.pipeline.topic_queue import (
    apply_slot_topic_remaining,
    pop_slot_topic,
    slot_topic_queues_from_blocks_config,
    with_preserved_slot_topic_queues,
)


def _meditation_schedule_cfg() -> dict:
    return {
        "version": 2,
        "steps": [
            {
                "id": "1",
                "type": "post_gen",
                "enabled": True,
                "config": {"mode": "ai", "user_input": "brief"},
            }
        ],
        "schedule": {
            "enabled": True,
            "frequency": "daily",
            "times": [MORNING_UTC, "09:11", "15:11"],
            "meditation_pipeline": True,
            "slot_topic_queues": {
                MORNING_UTC: ["Утро 1", "Утро 2"],
                "09:11": ["Обед 1"],
            },
            "slot_topic_history": {MORNING_UTC: ["Старая"]},
        },
    }


def test_pop_slot_topic_fifo():
    schedule = {
        "meditation_pipeline": True,
        "times": [MORNING_UTC],
        "slot_topic_queues": {MORNING_UTC: ["A", "B"]},
    }
    topic, remaining = pop_slot_topic(schedule, MORNING_UTC)
    assert topic == "A"
    assert remaining == ["B"]


def test_pop_slot_topic_empty_returns_none():
    schedule = {
        "meditation_pipeline": True,
        "times": [MORNING_UTC],
        "slot_topic_queues": {MORNING_UTC: []},
    }
    topic, remaining = pop_slot_topic(schedule, MORNING_UTC)
    assert topic is None
    assert remaining == []


def test_apply_slot_topic_remaining_updates_queue_and_history():
    cfg = _meditation_schedule_cfg()
    updated = apply_slot_topic_remaining(
        cfg,
        MORNING_UTC,
        ["Утро 2"],
        used_topic="Утро 1",
    )
    sched = updated["schedule"]
    assert sched["slot_topic_queues"][MORNING_UTC] == ["Утро 2"]
    assert "Утро 1" in sched["slot_topic_history"][MORNING_UTC]
    assert "Старая" in sched["slot_topic_history"][MORNING_UTC]


def test_with_preserved_slot_topic_queues_keeps_live():
    ui = {
        "schedule": {
            "meditation_pipeline": True,
            "times": [MORNING_UTC],
            "slot_topic_queues": {MORNING_UTC: ["Stale 1", "Stale 2"]},
            "slot_topic_history": {MORNING_UTC: []},
        }
    }
    live = _meditation_schedule_cfg()
    merged = with_preserved_slot_topic_queues(ui, live)
    live_queues = slot_topic_queues_from_blocks_config(live)
    assert merged["schedule"]["slot_topic_queues"] == live_queues
    assert merged["schedule"]["slot_topic_history"][MORNING_UTC] == ["Старая"]


def test_meditation_preset_normalizes_slot_queues():
    from app.application.pipeline.meditation_presets import build_meditation_blocks_v2

    v2 = build_meditation_blocks_v2()
    sched = v2["schedule"]
    assert sched["meditation_pipeline"] is True
    assert MORNING_UTC in sched["slot_topic_queues"]
    assert MORNING_UTC in sched["slot_sunor_presets"]
    assert sched["slot_sunor_presets"][MORNING_UTC]["generation_mode"] == "single"
    assert sched["slot_sunor_presets"][MORNING_UTC]["vocal_gender"] == "m"
    assert "male voice" in sched["slot_sunor_presets"][MORNING_UTC]["tags"].lower()
    morning_prompt = sched["slot_prompts"][MORNING_UTC]
    lunch_prompt = sched["slot_prompts"][LUNCH_UTC]
    assert "гендерно нейтральн" in morning_prompt
    assert "гендерно нейтральн" in lunch_prompt
    assert "без привязки к полу" in sched["slot_topic_gen_extra"][MORNING_UTC]
    assert sched["slot_sunor_presets"]["15:11"]["generation_mode"] == "merge_4tracks"
    assert sched["slot_sunor_presets"]["15:11"]["make_instrumental"] is True
    assert "instrumental meditation" in sched["slot_sunor_presets"]["15:11"]["tags"].lower()
    image_gen = next(s for s in v2["steps"] if s["type"] == "image_gen")
    assert image_gen["config"]["aspect_ratio"] == "16:9"
