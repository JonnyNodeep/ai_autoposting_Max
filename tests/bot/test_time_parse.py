from app.bot.handlers.channel_setup import _parse_time


def test_parse_time_accepts_valid_values():
    assert _parse_time("14:30") == (14, 30)
    assert _parse_time("9.05") == (9, 5)


def test_parse_time_rejects_invalid_minutes():
    assert _parse_time("12:60") is None
    assert _parse_time("07:99") is None


def test_parse_time_rejects_invalid_hours():
    assert _parse_time("24:00") is None
