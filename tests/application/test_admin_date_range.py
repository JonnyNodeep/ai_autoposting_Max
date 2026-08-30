from datetime import UTC, datetime

from app.presentation.admin.router import _parse_admin_date_range


def test_parse_admin_date_range_empty():
    dt_from, dt_to, raw_from, raw_to = _parse_admin_date_range("", "")
    assert dt_from is None
    assert dt_to is None
    assert raw_from == ""
    assert raw_to == ""


def test_parse_admin_date_range_inclusive_day():
    dt_from, dt_to, raw_from, raw_to = _parse_admin_date_range("2026-08-01", "2026-08-21")
    assert raw_from == "2026-08-01"
    assert raw_to == "2026-08-21"
    assert dt_from == datetime(2026, 8, 1, tzinfo=UTC)
    # exclusive end = next day
    assert dt_to == datetime(2026, 8, 22, tzinfo=UTC)


def test_parse_admin_date_range_swaps_inverted():
    dt_from, dt_to, raw_from, raw_to = _parse_admin_date_range("2026-08-21", "2026-08-01")
    assert raw_from == "2026-08-01"
    assert raw_to == "2026-08-21"
    assert dt_from == datetime(2026, 8, 1, tzinfo=UTC)
    assert dt_to == datetime(2026, 8, 22, tzinfo=UTC)
