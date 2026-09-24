from app.bot.handlers.ai_studio_meditation import (
    _parse_topics_action_callback,
    _repair_legacy_slot_keys,
    _slot_time_from_callback,
)

_PICK = "ai:meditation:topics:pick:"
_STYLE = "ai:meditation:style_ref:"


def test_slot_time_from_callback_with_colons():
    assert _slot_time_from_callback(f"{_PICK}04:30", _PICK) == "04:30"
    assert _slot_time_from_callback(f"{_STYLE}09:11", _STYLE) == "09:11"
    assert _slot_time_from_callback(f"{_STYLE}15:11", _STYLE) == "15:11"


def test_parse_topics_action_callback():
    assert _parse_topics_action_callback("ai:meditation:topics:04:30:add") == (
        "04:30",
        "add",
    )
    assert _parse_topics_action_callback("ai:meditation:topics:09:11:gen14") == (
        "09:11",
        "gen14",
    )
    assert _parse_topics_action_callback("ai:meditation:topics:pick:04:30") is None


def test_repair_legacy_slot_keys():
    schedule = {
        "times": ["04:30", "09:11", "15:11"],
        "slot_image_refs": {"30": "/a.png", "11": "/b.png"},
        "slot_topic_queues": {"30": ["T1"]},
    }
    fixed = _repair_legacy_slot_keys(schedule)
    assert fixed["slot_image_refs"]["04:30"] == "/a.png"
    assert fixed["slot_image_refs"]["09:11"] == "/b.png"
    assert "30" not in fixed["slot_image_refs"]
    assert fixed["slot_topic_queues"]["04:30"] == ["T1"]
