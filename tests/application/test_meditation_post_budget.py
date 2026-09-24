"""Meditation post length budget."""

from app.application.pipeline.meditation_post_budget import (
    POST_MAX_PUBLISHED,
    compute_meditation_body_max_chars,
    estimate_meditation_footer_reserve,
)


def test_footer_reserve_subscribe_only():
    reserve = estimate_meditation_footer_reserve(
        {"add_channel_link": True},
        channel_link="https://max.ru/test-channel",
        channel_title="Медитации",
    )
    assert reserve > 40
    body_max = compute_meditation_body_max_chars(reserve)
    assert body_max < POST_MAX_PUBLISHED
    assert body_max + reserve <= POST_MAX_PUBLISHED or body_max >= 220


def test_body_max_defaults_without_footer():
    assert compute_meditation_body_max_chars(0) == POST_MAX_PUBLISHED
