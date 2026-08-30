from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from app.config import settings


def parse_max_user_id_list(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in (raw or "").split(","):
        piece = part.strip()
        if not piece:
            continue
        try:
            ids.add(int(piece))
        except ValueError:
            continue
    return frozenset(ids)


@lru_cache
def _rss_whitelist_env() -> frozenset[int]:
    return parse_max_user_id_list(settings.features.rss_whitelist)


@lru_cache
def _video_whitelist_env() -> frozenset[int]:
    return parse_max_user_id_list(settings.features.video_whitelist)


@lru_cache
def _audio_whitelist_env() -> frozenset[int]:
    return parse_max_user_id_list(settings.features.audio_whitelist)


@lru_cache
def _drive_whitelist_env() -> frozenset[int]:
    return parse_max_user_id_list(settings.features.drive_whitelist)


@lru_cache
def _high_freq_whitelist_env() -> frozenset[int]:
    return parse_max_user_id_list(settings.features.high_freq_whitelist)


# Back-compat aliases for tests that patch these names.
_rss_whitelist = _rss_whitelist_env
_video_whitelist = _video_whitelist_env
_audio_whitelist = _audio_whitelist_env
_drive_whitelist = _drive_whitelist_env
_high_freq_whitelist = _high_freq_whitelist_env


_runtime_whitelists: dict[str, frozenset[int]] = {}


def set_runtime_whitelists(
    *,
    rss: str | None = None,
    video: str | None = None,
    audio: str | None = None,
    drive: str | None = None,
    high_freq: str | None = None,
) -> None:
    """Merge DB whitelists with env (union)."""
    global _runtime_whitelists
    if rss is not None:
        _runtime_whitelists["rss"] = _rss_whitelist() | parse_max_user_id_list(rss)
    if video is not None:
        _runtime_whitelists["video"] = _video_whitelist() | parse_max_user_id_list(video)
    if audio is not None:
        _runtime_whitelists["audio"] = _audio_whitelist() | parse_max_user_id_list(audio)
    if drive is not None:
        _runtime_whitelists["drive"] = _drive_whitelist() | parse_max_user_id_list(drive)
    if high_freq is not None:
        _runtime_whitelists["high_freq"] = _high_freq_whitelist() | parse_max_user_id_list(high_freq)


def _in_whitelist(max_user_id: int | None, whitelist: frozenset[int]) -> bool:
    if max_user_id is None:
        return False
    return int(max_user_id) in whitelist


def rss_allowed(max_user_id: int | None) -> bool:
    wl = _runtime_whitelists.get("rss", _rss_whitelist())
    return _in_whitelist(max_user_id, wl)


def video_allowed(max_user_id: int | None) -> bool:
    wl = _runtime_whitelists.get("video", _video_whitelist())
    return _in_whitelist(max_user_id, wl)


def audio_allowed(max_user_id: int | None) -> bool:
    wl = _runtime_whitelists.get("audio", _audio_whitelist())
    return _in_whitelist(max_user_id, wl)


def drive_allowed(max_user_id: int | None) -> bool:
    wl = _runtime_whitelists.get("drive", _drive_whitelist())
    return _in_whitelist(max_user_id, wl)


def high_freq_allowed(max_user_id: int | None) -> bool:
    wl = _runtime_whitelists.get("high_freq", _high_freq_whitelist())
    return _in_whitelist(max_user_id, wl)


_PREMIUM_BLOCK_KEYS = ("news_rss", "video_gen", "story_gen", "tts_gen", "sunor_gen", "drive_video")


def sanitize_premium_blocks(ui_blocks: dict[str, Any], max_user_id: int | None) -> dict[str, Any]:
    """Disable premium blocks for users not on the respective whitelists."""
    out = copy.deepcopy(ui_blocks)
    if not rss_allowed(max_user_id):
        block = dict(out.get("news_rss") or {})
        block["enabled"] = False
        out["news_rss"] = block
    if not video_allowed(max_user_id):
        block = dict(out.get("video_gen") or {})
        block["enabled"] = False
        out["video_gen"] = block
    if not audio_allowed(max_user_id):
        for key in ("story_gen", "tts_gen", "sunor_gen"):
            block = dict(out.get(key) or {})
            block["enabled"] = False
            out[key] = block
    if not drive_allowed(max_user_id):
        block = dict(out.get("drive_video") or {})
        block["enabled"] = False
        out["drive_video"] = block
    return out


def sanitize_premium_blocks_config(
    blocks_config: dict[str, Any],
    max_user_id: int | None,
) -> dict[str, Any]:
    """Sanitize v2 pipeline config (version=2) or UI dict before storage."""
    if not blocks_config:
        return blocks_config
    if blocks_config.get("version") == 2:
        out = copy.deepcopy(blocks_config)
        steps = list(out.get("steps") or [])
        for step in steps:
            if not isinstance(step, dict):
                continue
            stype = str(step.get("type") or "")
            if stype == "news_rss" and not rss_allowed(max_user_id):
                step["enabled"] = False
            elif stype == "video_gen" and not video_allowed(max_user_id):
                step["enabled"] = False
            elif stype in ("story_gen", "tts_gen", "sunor_gen") and not audio_allowed(max_user_id):
                step["enabled"] = False
            elif stype == "drive_video" and not drive_allowed(max_user_id):
                step["enabled"] = False
        out["steps"] = steps
        news = dict(out.get("news_rss") or {})
        if not rss_allowed(max_user_id):
            news["enabled"] = False
            out["news_rss"] = news
        drive = dict(out.get("drive_video") or {})
        if not drive_allowed(max_user_id):
            drive["enabled"] = False
            out["drive_video"] = drive
        return out
    return sanitize_premium_blocks(blocks_config, max_user_id)


def premium_invite_message(feature: str = "Эта функция") -> str:
    return f"{feature} доступна по приглашению. Обратитесь к администратору бота."
