from app.bot.schedule_frequency import (
    expected_slots,
    freq_label,
    is_high_freq,
    is_multi_slot_freq,
)


def test_expected_slots():
    assert expected_slots("daily") == 1
    assert expected_slots("5x_day") == 5
    assert expected_slots("8x_day") == 8


def test_is_high_freq():
    assert is_high_freq("6x_day") is True
    assert is_high_freq("7x_day") is True
    assert is_high_freq("8x_day") is True
    assert is_high_freq("5x_day") is False
    assert is_high_freq("daily") is False


def test_is_multi_slot_freq():
    assert is_multi_slot_freq("3x_day") is True
    assert is_multi_slot_freq("daily") is False


def test_freq_label():
    assert freq_label("8x_day") == "8 раз в день"
    assert freq_label("8x_day", short=True) == "8×/день"
