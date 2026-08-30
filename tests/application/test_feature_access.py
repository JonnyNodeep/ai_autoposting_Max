from app.application.auth.feature_access import (
    audio_allowed,
    drive_allowed,
    high_freq_allowed,
    parse_max_user_id_list,
    rss_allowed,
    sanitize_premium_blocks,
    set_runtime_whitelists,
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
    monkeypatch.setattr(settings.features, "drive_whitelist", "")
    monkeypatch.setattr(settings.features, "high_freq_whitelist", "")
    from app.application.auth import feature_access as fa

    fa._rss_whitelist.cache_clear()
    fa._video_whitelist.cache_clear()
    fa._audio_whitelist.cache_clear()
    fa._drive_whitelist.cache_clear()
    fa._high_freq_whitelist.cache_clear()

    assert rss_allowed(123) is False
    assert video_allowed(123) is False
    assert audio_allowed(123) is False
    assert drive_allowed(123) is False
    assert high_freq_allowed(123) is False
    assert rss_allowed(None) is False
    assert high_freq_allowed(None) is False


def test_whitelist_allowed(monkeypatch):
    monkeypatch.setattr(settings.features, "rss_whitelist", "100,200")
    monkeypatch.setattr(settings.features, "video_whitelist", "100")
    monkeypatch.setattr(settings.features, "audio_whitelist", "200")
    monkeypatch.setattr(settings.features, "drive_whitelist", "300")
    monkeypatch.setattr(settings.features, "high_freq_whitelist", "400,500")
    from app.application.auth import feature_access as fa

    fa._rss_whitelist.cache_clear()
    fa._video_whitelist.cache_clear()
    fa._audio_whitelist.cache_clear()
    fa._drive_whitelist.cache_clear()
    fa._high_freq_whitelist.cache_clear()

    assert rss_allowed(100) is True
    assert rss_allowed(200) is True
    assert rss_allowed(300) is False
    assert video_allowed(100) is True
    assert video_allowed(200) is False
    assert audio_allowed(200) is True
    assert audio_allowed(100) is False
    assert drive_allowed(300) is True
    assert drive_allowed(100) is False
    assert high_freq_allowed(400) is True
    assert high_freq_allowed(500) is True
    assert high_freq_allowed(100) is False


def test_high_freq_runtime_whitelist(monkeypatch):
    monkeypatch.setattr(settings.features, "high_freq_whitelist", "100")
    from app.application.auth import feature_access as fa

    fa._high_freq_whitelist.cache_clear()
    set_runtime_whitelists(high_freq="200")
    assert high_freq_allowed(100) is True
    assert high_freq_allowed(200) is True
    assert high_freq_allowed(300) is False


def test_sanitize_premium_blocks():
    blocks = {
        "news_rss": {"enabled": True, "feeds": ["https://x"]},
        "video_gen": {"enabled": True},
        "story_gen": {"enabled": True},
        "tts_gen": {"enabled": True},
        "sunor_gen": {"enabled": True},
        "drive_video": {"enabled": True, "folder_id": "abc"},
        "post_gen": {"enabled": True, "add_channel_link": True},
        "schedule": {"enabled": True},
        "image_gen": {"enabled": True},
    }
    out = sanitize_premium_blocks(blocks, max_user_id=None)
    assert out["news_rss"]["enabled"] is False
    assert out["video_gen"]["enabled"] is False
    assert out["story_gen"]["enabled"] is False
    assert out["tts_gen"]["enabled"] is False
    assert out["sunor_gen"]["enabled"] is False
    assert out["drive_video"]["enabled"] is False
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
    monkeypatch.setattr(settings.features, "drive_whitelist", "")
    from app.application.auth import feature_access as fa

    fa._rss_whitelist.cache_clear()
    fa._video_whitelist.cache_clear()
    fa._audio_whitelist.cache_clear()
    fa._drive_whitelist.cache_clear()

    kb = InlineKeyboardBuilder.ai_studio_blocks({}, max_user_id=999)
    payloads = []
    for row in kb["payload"]["buttons"]:
        for btn in row:
            payloads.append(btn.get("payload", ""))

    assert "ai:edit:news_rss" not in payloads
    assert "ai:edit:drive_video" not in payloads
    assert "ai:edit:video_gen" not in payloads
    assert "ai:edit:tts_gen" not in payloads
    assert "ai:edit:sunor_gen" not in payloads
    assert "ai:edit:schedule" in payloads
    assert "ai:edit:post_gen" in payloads
    assert "ai:blocks:test" in payloads
    assert "ai_studio" in payloads


def test_frequency_presets_high_freq_whitelist(monkeypatch):
    from app.bot.keyboards.builder import InlineKeyboardBuilder

    monkeypatch.setattr(settings.features, "high_freq_whitelist", "")
    from app.application.auth import feature_access as fa

    fa._high_freq_whitelist.cache_clear()

    kb_plain = InlineKeyboardBuilder.frequency_presets(max_user_id=999)
    payloads_plain = [
        btn.get("payload", "")
        for row in kb_plain["payload"]["buttons"]
        for btn in row
    ]
    assert "setup:frequency:6x_day" not in payloads_plain
    assert "setup:frequency:5x_day" in payloads_plain

    monkeypatch.setattr(settings.features, "high_freq_whitelist", "999")
    fa._high_freq_whitelist.cache_clear()
    kb_wl = InlineKeyboardBuilder.frequency_presets(max_user_id=999)
    payloads_wl = [
        btn.get("payload", "")
        for row in kb_wl["payload"]["buttons"]
        for btn in row
    ]
    assert "setup:frequency:8x_day" in payloads_wl
    assert "setup:frequency:6x_day" in payloads_wl
