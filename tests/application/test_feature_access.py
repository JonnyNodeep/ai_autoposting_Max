from app.application.auth.feature_access import (
    audio_allowed,
    parse_max_user_id_list,
    rss_allowed,
    sanitize_premium_blocks,
    video_allowed,
)
from app.config import settings


def test_parse_max_user_id_list():
    assert parse_max_user_id_list("") == frozenset()
    assert parse_max_user_id_list("123, 456 ,789") == frozenset({123, 456, 789})
    assert parse_max_user_id_list("bad,42") == frozenset({42})


def test_whitelist_strict_empty(monkeypatch):
    monkeypatch.setattr(settings.features, "rss_whitelist", "")
    monkeypatch.setattr(settings.features, "video_whitelist", "")
    monkeypatch.setattr(settings.features, "audio_whitelist", "")
    from app.application.auth import feature_access as fa

    fa._rss_whitelist.cache_clear()
    fa._video_whitelist.cache_clear()
    fa._audio_whitelist.cache_clear()

    assert rss_allowed(123) is False
    assert video_allowed(123) is False
    assert audio_allowed(123) is False
    assert rss_allowed(None) is False


def test_whitelist_allowed(monkeypatch):
    monkeypatch.setattr(settings.features, "rss_whitelist", "100,200")
    monkeypatch.setattr(settings.features, "video_whitelist", "100")
    monkeypatch.setattr(settings.features, "audio_whitelist", "200")
    from app.application.auth import feature_access as fa

    fa._rss_whitelist.cache_clear()
    fa._video_whitelist.cache_clear()
    fa._audio_whitelist.cache_clear()

    assert rss_allowed(100) is True
    assert rss_allowed(200) is True
    assert rss_allowed(300) is False
    assert video_allowed(100) is True
    assert video_allowed(200) is False
    assert audio_allowed(200) is True
    assert audio_allowed(100) is False


def test_sanitize_premium_blocks():
    blocks = {
        "news_rss": {"enabled": True, "feeds": ["https://x"]},
        "video_gen": {"enabled": True},
        "story_gen": {"enabled": True},
        "tts_gen": {"enabled": True},
        "post_gen": {"enabled": True, "add_channel_link": True},
        "schedule": {"enabled": True},
        "image_gen": {"enabled": True},
    }
    out = sanitize_premium_blocks(blocks, max_user_id=None)
    assert out["news_rss"]["enabled"] is False
    assert out["video_gen"]["enabled"] is False
    assert out["story_gen"]["enabled"] is False
    assert out["tts_gen"]["enabled"] is False
    assert out["post_gen"]["enabled"] is True
    assert out["post_gen"]["add_channel_link"] is True
    assert out["schedule"]["enabled"] is True
    assert out["image_gen"]["enabled"] is True
    # original unchanged
    assert blocks["news_rss"]["enabled"] is True


def test_ai_studio_blocks_hides_premium_without_whitelist(monkeypatch):
    from app.bot.keyboards.builder import InlineKeyboardBuilder
    from app.config import settings

    monkeypatch.setattr(settings.features, "rss_whitelist", "")
    monkeypatch.setattr(settings.features, "video_whitelist", "")
    monkeypatch.setattr(settings.features, "audio_whitelist", "")
    from app.application.auth import feature_access as fa

    fa._rss_whitelist.cache_clear()
    fa._video_whitelist.cache_clear()
    fa._audio_whitelist.cache_clear()

    kb = InlineKeyboardBuilder.ai_studio_blocks({}, max_user_id=999)
    payloads = []
    for row in kb["payload"]["buttons"]:
        for btn in row:
            payloads.append(btn.get("payload", ""))

    assert "ai:edit:news_rss" not in payloads
    assert "ai:edit:video_gen" not in payloads
    assert "ai:edit:tts_gen" not in payloads
    assert "ai:edit:schedule" in payloads
    assert "ai:edit:post_gen" in payloads
    assert "ai:blocks:test" in payloads
