from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock

import pytest

from app.application.pipeline.rss_monitor import (
    RssNewsItem,
    filter_new_items,
    format_keywords_review,
    format_publish_window_label,
    generate_keywords_for_topic,
    is_rss_trigger,
    is_within_publish_window,
    normalize_news_rss,
    parse_feed_bytes,
    parse_hhmm,
    pick_next,
    preset_keywords,
)
from app.application.pipeline.normalize import normalize_blocks_config, steps_to_ui_dict
from app.bot.states.ai_studio import DEFAULT_BLOCKS


SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Demo</title>
    <item>
      <title>Bitcoin ETF approved</title>
      <link>https://example.com/a</link>
      <guid>guid-a</guid>
      <pubDate>Sun, 02 Aug 2026 08:00:00 GMT</pubDate>
      <description>Fresh crypto news &lt;img src="https://cdn.example.com/btc.jpg" /&gt;</description>
      <enclosure url="https://cdn.example.com/btc-large.jpg" type="image/jpeg" />
    </item>
    <item>
      <title>Giveaway airdrop promo</title>
      <link>https://example.com/b</link>
      <guid>guid-b</guid>
      <pubDate>Sun, 02 Aug 2026 09:00:00 GMT</pubDate>
      <description>Spam</description>
    </item>
  </channel>
</rss>
"""


SAMPLE_RSS_MEDIA = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Demo</title>
    <item>
      <title>Park opened</title>
      <link>https://ekb.example/news/1</link>
      <guid>g1</guid>
      <media:content url="https://ekb.example/img/park.jpg" medium="image" type="image/jpeg" />
      <description>New park</description>
    </item>
  </channel>
</rss>
"""


def test_normalize_news_rss_defaults_and_keywords():
    n = normalize_news_rss(
        {
            "enabled": True,
            "feeds": [" https://a "],
            "include_keywords": ["BTC", "btc", "Ethereum"],
            "exclude_keywords": ["giveaway"],
            "niche": "crypto",
        }
    )
    assert n["feeds"] == ["https://a"]
    assert n["include_keywords"] == ["BTC", "Ethereum"]
    assert n["exclude_keywords"] == ["giveaway"]
    assert n["niche"] == "crypto"
    assert n["publish_from_msk"] == "09:00"
    assert n["publish_until_msk"] == "22:00"


def test_normalize_preserves_publish_window():
    n = normalize_news_rss(
        {"publish_from_msk": "8:00", "publish_until_msk": "23:00", "publish_from_msk_bad": "xx"}
    )
    assert n["publish_from_msk"] == "08:00"
    assert n["publish_until_msk"] == "23:00"
    assert parse_hhmm("25:00", default="09:00") == "09:00"


def test_is_within_publish_window_default_daytime():
    # 12:00 MSK = 09:00 UTC
    noon_msk_as_utc = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
    assert is_within_publish_window(noon_msk_as_utc, "09:00", "22:00") is True
    # 08:30 MSK = 05:30 UTC
    early = datetime(2026, 8, 2, 5, 30, tzinfo=UTC)
    assert is_within_publish_window(early, "09:00", "22:00") is False
    # 22:00 MSK = 19:00 UTC — until is exclusive
    at_end = datetime(2026, 8, 2, 19, 0, tzinfo=UTC)
    assert is_within_publish_window(at_end, "09:00", "22:00") is False
    # 21:59 MSK = 18:59 UTC
    almost_end = datetime(2026, 8, 2, 18, 59, tzinfo=UTC)
    assert is_within_publish_window(almost_end, "09:00", "22:00") is True


def test_is_within_publish_window_247():
    night = datetime(2026, 8, 2, 1, 0, tzinfo=UTC)
    assert is_within_publish_window(night, "00:00", "00:00") is True
    assert format_publish_window_label("00:00", "00:00") == "круглосуточно"
    assert "МСК" in format_publish_window_label("09:00", "22:00")


def test_normalize_blocks_includes_news_rss():
    v2 = normalize_blocks_config(DEFAULT_BLOCKS)
    assert v2["news_rss"]["enabled"] is False
    back = steps_to_ui_dict(v2)
    assert "news_rss" in back


def test_parse_publish_window_text():
    from app.application.pipeline.rss_monitor import parse_publish_window_text

    assert parse_publish_window_text("09:00-22:00") == ("09:00", "22:00")
    assert parse_publish_window_text("9:00 – 21:30") == ("09:00", "21:30")
    assert parse_publish_window_text("с 08:00 до 23:00") == ("08:00", "23:00")
    assert parse_publish_window_text("00:00-00:00") == ("00:00", "00:00")
    assert parse_publish_window_text("10.00-20.00") == ("10:00", "20:00")
    assert parse_publish_window_text("bad") is None
    assert parse_publish_window_text("25:00-22:00") is None


def test_is_rss_trigger():
    assert is_rss_trigger({}) is False
    assert (
        is_rss_trigger(
            {"news_rss": {"enabled": True, "feeds": ["https://x"], "mode": "on_new"}}
        )
        is True
    )
    assert (
        is_rss_trigger(
            {
                "news_rss": {
                    "enabled": True,
                    "feeds": [],
                    "sites": ["https://example.com/news"],
                    "mode": "on_new",
                }
            }
        )
        is True
    )
    assert (
        is_rss_trigger(
            {"news_rss": {"enabled": True, "feeds": [], "sites": [], "mode": "on_new"}}
        )
        is False
    )


def test_normalize_news_rss_sites():
    n = normalize_news_rss(
        {"enabled": True, "sites": [" https://a/news ", "", "https://b/news"]}
    )
    assert n["sites"] == ["https://a/news", "https://b/news"]


def test_normalize_blocks_preserves_sites():
    v2 = normalize_blocks_config(
        {
            **DEFAULT_BLOCKS,
            "news_rss": {
                **DEFAULT_BLOCKS["news_rss"],
                "enabled": True,
                "sites": ["https://example.com/news"],
            },
        }
    )
    assert v2["news_rss"]["sites"] == ["https://example.com/news"]
    back = steps_to_ui_dict(v2)
    assert back["news_rss"]["sites"] == ["https://example.com/news"]


def test_discover_feed_urls_from_html():
    from app.application.pipeline.rss_monitor import discover_feed_urls

    html = """
    <html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml" title="RSS">
      <link rel="stylesheet" href="/style.css">
      <link rel="alternate" type="application/atom+xml" href="https://cdn.example.com/atom.xml">
    </head></html>
    """
    found = discover_feed_urls("https://example.com/news", html)
    assert "https://example.com/feed.xml" in found
    assert "https://cdn.example.com/atom.xml" in found


def test_parse_html_listing_same_host_articles():
    from app.application.pipeline.rss_monitor import parse_html_listing

    html = """
    <html><body>
      <a href="/news/city-park-opened">В Екатеринбурге открыли новый парк у реки</a>
      <a href="/about">О нас</a>
      <a href="https://other.com/news/x">Чужой сайт</a>
      <a href="/news/tram-line">Запустили новую ветку трамвая в центре</a>
      <a href="/tag/ekb">тег</a>
    </body></html>
    """
    items = parse_html_listing(html, "https://ekb.example/news")
    urls = {it.url for it in items}
    assert "https://ekb.example/news/city-park-opened" in urls
    assert "https://ekb.example/news/tram-line" in urls
    assert "https://other.com/news/x" not in urls
    assert all(it.feed_url == "https://ekb.example/news" for it in items)



def test_parse_and_keyword_filter():
    now = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
    items = parse_feed_bytes(SAMPLE_RSS, "https://example.com/rss")
    assert len(items) == 2
    filtered = filter_new_items(
        items,
        seen_guids=set(),
        seen_urls=set(),
        max_age_hours=24,
        include_keywords=["bitcoin", "crypto"],
        exclude_keywords=["giveaway", "airdrop"],
        now=now,
    )
    assert len(filtered) == 1
    assert "Bitcoin" in filtered[0].title


def test_filter_skips_seen_and_old():
    now = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
    items = [
        RssNewsItem(
            feed_url="a",
            guid="1",
            url="u1",
            title="New AI model",
            summary="",
            published_at=now - timedelta(hours=1),
        ),
        RssNewsItem(
            feed_url="a",
            guid="2",
            url="u2",
            title="Old",
            summary="",
            published_at=now - timedelta(days=3),
        ),
    ]
    filtered = filter_new_items(
        items,
        seen_guids={"1"},
        seen_urls=set(),
        max_age_hours=24,
        now=now,
    )
    assert filtered == []


def test_preset_keywords():
    p = preset_keywords("crypto")
    assert "bitcoin" in [x.casefold() for x in p["include"]]
    assert p["exclude"]


@pytest.mark.asyncio
async def test_generate_keywords_parses_ai_json():
    client = AsyncMock()
    client.generate_text = AsyncMock(
        return_value='{"include": ["ai", "openai"], "exclude": ["promo"], "reason": "ok"}'
    )
    result = await generate_keywords_for_topic(client, niche="it", topic_brief="IT")
    assert result["source"] == "ai"
    assert "ai" in result["include"]
    assert "promo" in result["exclude"]


@pytest.mark.asyncio
async def test_generate_keywords_falls_back_to_preset():
    client = AsyncMock()
    client.generate_text = AsyncMock(side_effect=RuntimeError("down"))
    result = await generate_keywords_for_topic(client, niche="crypto")
    assert result["source"] == "preset"
    assert result["include"]


def test_format_keywords_review():
    text = format_keywords_review(
        niche="crypto",
        include=["bitcoin"],
        exclude=["giveaway"],
        reason="test",
    )
    assert "bitcoin" in text
    assert "giveaway" in text
    assert pick_next([]) is None


def test_parse_feed_extracts_enclosure_image():
    items = parse_feed_bytes(SAMPLE_RSS, "https://example.com/rss")
    assert items[0].image_url == "https://cdn.example.com/btc-large.jpg"
    assert items[1].image_url is None


def test_parse_feed_extracts_media_content_image():
    items = parse_feed_bytes(SAMPLE_RSS_MEDIA, "https://ekb.example/rss")
    assert items[0].image_url == "https://ekb.example/img/park.jpg"


def test_extract_og_image():
    from app.application.pipeline.rss_monitor import extract_og_image

    html = """
    <html><head>
      <meta property="og:image" content="/photos/hero.jpg" />
    </head></html>
    """
    assert extract_og_image(html, page_url="https://e1.ru/news/1") == "https://e1.ru/photos/hero.jpg"
