from app.presentation.api.webhook import _extract_dedup_id


def test_extract_dedup_id_prefers_callback_id():
    update = {
        "update_type": "message_callback",
        "callback": {"callback_id": "cb-123"},
        "message": {"message_id": 99},
    }
    assert _extract_dedup_id(update) == "cb-123"


def test_extract_dedup_id_falls_back_to_message_id():
    update = {
        "update_type": "message_created",
        "message": {"message_id": 42},
    }
    assert _extract_dedup_id(update) == "42"


def test_extract_dedup_id_returns_empty_for_unknown_payload():
    assert _extract_dedup_id({"update_type": "bot_started"}) == ""
