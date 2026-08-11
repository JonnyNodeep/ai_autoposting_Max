from __future__ import annotations

import copy
import uuid
from typing import Any

from app.application.pipeline.rss_monitor import DEFAULT_NEWS_RSS, normalize_news_rss
from app.application.pipeline.topic_queue import normalize_topic_queue

STEP_ORDER = (
    "story_gen",
    "image_prompt",
    "image_gen",
    "video_gen",
    "tts_gen",
    "post_gen",
)

_CONFIG_KEYS = {
    "image_gen": ("model", "add_watermark", "allow_text"),
    "image_prompt": ("mode", "user_description", "generated_prompt", "instruction", "use_visual_style"),
    "video_gen": (
        "model",
        "duration",
        "mode",
        "resolution",
        "aspect_ratio",
        "fixed_lens",
        "generate_audio",
        "fallback_model",
        "prompt_mode",
        "user_description",
        "generated_prompt",
    ),
    "story_gen": (
        "mode",
        "user_input",
        "target_minutes",
        "age_range",
        "format",
        "topic_queue",
        "generated_story",
        "generated_caption",
    ),
    "tts_gen": (
        "provider",
        "model",
        "voice",
        "speed",
        "role",
        "response_format",
        "instructions",
        "instructions_preset",
    ),
    "post_gen": (
        "mode",
        "user_input",
        "generated_post",
        "add_channel_link",
        "bold_headings",
        "use_emoji",
        "comments_enabled",
        "topic_queue",
    ),
    "schedule": ("frequency", "times", "per_slot_prompts", "slot_prompts"),
    "news_rss": (
        "feeds",
        "sites",
        "mode",
        "poll_interval_minutes",
        "max_age_hours",
        "max_posts_per_hour",
        "publish_from_msk",
        "publish_until_msk",
        "niche",
        "topic_brief",
        "include_keywords",
        "exclude_keywords",
        "keywords_source",
    ),
}


def _normalize_post_gen_config(config: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(config or {})
    cfg["topic_queue"] = normalize_topic_queue(cfg.get("topic_queue"))
    return cfg


def _normalize_story_gen_config(config: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(config or {})
    cfg["topic_queue"] = normalize_topic_queue(cfg.get("topic_queue"))
    try:
        cfg["target_minutes"] = max(2, min(int(cfg.get("target_minutes") or 5), 12))
    except (TypeError, ValueError):
        cfg["target_minutes"] = 5
    if "age_range" in cfg:
        cfg["age_range"] = str(cfg.get("age_range") or "3-6")[:32]
    if "format" in cfg:
        fmt = str(cfg.get("format") or "fairy_tale").strip() or "fairy_tale"
        if fmt in ("bedtime", "fairy_tale", "podcast"):
            cfg["format"] = "fairy_tale" if fmt == "bedtime" else fmt
        else:
            cfg["format"] = "fairy_tale"
    if "mode" in cfg:
        mode = str(cfg.get("mode") or "ai").strip()
        cfg["mode"] = mode if mode in ("ai", "fixed") else "ai"
    return cfg


def _normalize_tts_gen_config(config: dict[str, Any]) -> dict[str, Any]:
    from app.application.pipeline.tts_instructions import (
        DEFAULT_TTS_INSTRUCTIONS,
        DEFAULT_TTS_INSTRUCTIONS_PRESET,
        TTS_INSTRUCTION_PRESETS,
    )
    from app.application.pipeline.tts_voices import (
        DEFAULT_OPENAI_SPEED,
        DEFAULT_OPENAI_VOICE,
        DEFAULT_SPEECHKIT_ROLE,
        DEFAULT_SPEECHKIT_SPEED,
        DEFAULT_SPEECHKIT_VOICE,
        TTS_PROVIDER_OPENAI,
        TTS_PROVIDER_SPEECHKIT,
        TTS_PROVIDERS,
        openai_voice_ids,
        resolve_role,
        speechkit_voice_ids,
    )

    cfg = dict(config or {})
    if "provider" in cfg and str(cfg.get("provider") or "").strip():
        provider = str(cfg.get("provider")).strip().lower()
    else:
        # Legacy pipelines had no provider field → OpenAI.
        provider = TTS_PROVIDER_OPENAI
    if provider not in TTS_PROVIDERS:
        provider = TTS_PROVIDER_OPENAI
    cfg["provider"] = provider

    model = str(cfg.get("model") or "gpt-4o-mini-tts").strip() or "gpt-4o-mini-tts"
    if model in ("tts-1", "tts-1-hd"):
        model = "gpt-4o-mini-tts"
    cfg["model"] = model
    cfg["response_format"] = str(cfg.get("response_format") or "mp3").strip() or "mp3"

    if provider == TTS_PROVIDER_SPEECHKIT:
        voice = str(cfg.get("voice") or DEFAULT_SPEECHKIT_VOICE).strip() or DEFAULT_SPEECHKIT_VOICE
        if voice not in speechkit_voice_ids():
            voice = DEFAULT_SPEECHKIT_VOICE
        cfg["voice"] = voice
        try:
            cfg["speed"] = max(0.1, min(float(cfg.get("speed", DEFAULT_SPEECHKIT_SPEED)), 3.0))
        except (TypeError, ValueError):
            cfg["speed"] = DEFAULT_SPEECHKIT_SPEED
        role = resolve_role(voice, str(cfg.get("role") or DEFAULT_SPEECHKIT_ROLE))
        cfg["role"] = role or DEFAULT_SPEECHKIT_ROLE
    else:
        voice = str(cfg.get("voice") or DEFAULT_OPENAI_VOICE).strip() or DEFAULT_OPENAI_VOICE
        if voice not in openai_voice_ids():
            # Migrating from SpeechKit voice while on openai
            if voice in speechkit_voice_ids():
                voice = DEFAULT_OPENAI_VOICE
            else:
                voice = DEFAULT_OPENAI_VOICE
        cfg["voice"] = voice
        try:
            cfg["speed"] = max(0.25, min(float(cfg.get("speed", DEFAULT_OPENAI_SPEED)), 4.0))
        except (TypeError, ValueError):
            cfg["speed"] = DEFAULT_OPENAI_SPEED
        cfg["role"] = str(cfg.get("role") or "").strip()

    preset = str(cfg.get("instructions_preset") or "").strip() or DEFAULT_TTS_INSTRUCTIONS_PRESET
    if preset not in TTS_INSTRUCTION_PRESETS and preset != "custom":
        preset = DEFAULT_TTS_INSTRUCTIONS_PRESET
    cfg["instructions_preset"] = preset

    instructions = str(cfg.get("instructions") or "").strip()
    if not instructions:
        if preset in TTS_INSTRUCTION_PRESETS:
            instructions = TTS_INSTRUCTION_PRESETS[preset]
        else:
            instructions = DEFAULT_TTS_INSTRUCTIONS
    cfg["instructions"] = instructions[:800]
    return cfg


def _new_step_id() -> str:
    return uuid.uuid4().hex[:12]


def _split_block_dict(block_id: str, data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    data = dict(data or {})
    enabled = bool(data.pop("enabled", False))
    known = _CONFIG_KEYS.get(block_id, ())
    config = {k: data[k] for k in known if k in data}
    for k, v in data.items():
        if k not in config and k != "enabled":
            config[k] = v
    return enabled, config


def is_v2(config: Any) -> bool:
    return isinstance(config, dict) and config.get("version") == 2 and "steps" in config


def _normalize_slot_prompts(raw: Any, times: list[str]) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    allowed = set(times)
    out: dict[str, str] = {}
    for key, value in raw.items():
        time_key = str(key).strip()
        if time_key not in allowed:
            continue
        text = str(value or "").strip()
        if text:
            out[time_key] = text[:4000]
    return out


def _normalize_schedule(raw: Any) -> dict[str, Any]:
    schedule = copy.deepcopy(raw or {"enabled": False, "frequency": "daily", "times": []})
    if not isinstance(schedule, dict):
        schedule = {"enabled": False, "frequency": "daily", "times": []}
    if "enabled" not in schedule:
        schedule["enabled"] = False
    if "frequency" not in schedule:
        schedule["frequency"] = "daily"
    if "times" not in schedule:
        schedule["times"] = []
    times = [str(t).strip() for t in (schedule.get("times") or []) if str(t).strip()]
    schedule["times"] = times
    schedule["per_slot_prompts"] = bool(schedule.get("per_slot_prompts", False))
    schedule["slot_prompts"] = _normalize_slot_prompts(schedule.get("slot_prompts"), times)
    if not schedule["per_slot_prompts"]:
        schedule["slot_prompts"] = {}
    return schedule


def resolve_post_brief(
    schedule: dict[str, Any] | None,
    post_cfg: dict[str, Any] | None,
    slot_time: str | None = None,
) -> str:
    """Pick slot-specific brief when enabled, else fall back to post_gen.user_input."""
    schedule = schedule or {}
    post_cfg = post_cfg or {}
    if schedule.get("per_slot_prompts") and slot_time:
        prompts = schedule.get("slot_prompts") or {}
        slot_brief = str(prompts.get(slot_time) or "").strip()
        if slot_brief:
            return slot_brief
    return str(post_cfg.get("user_input") or "").strip()


def _migrate_story_topic_queue_into_post(steps: list[dict[str, Any]]) -> None:
    """Move leftover story_gen.topic_queue into post_gen when post queue is empty."""
    story_step = None
    post_step = None
    for step in steps:
        if step.get("type") == "story_gen":
            story_step = step
        elif step.get("type") == "post_gen":
            post_step = step
    if story_step is None:
        return
    story_cfg = dict(story_step.get("config") or {})
    story_queue = normalize_topic_queue(story_cfg.get("topic_queue"))
    if not story_queue:
        return
    if post_step is None:
        post_step = {
            "id": _new_step_id(),
            "type": "post_gen",
            "enabled": False,
            "config": {},
        }
        steps.append(post_step)
    post_cfg = dict(post_step.get("config") or {})
    post_queue = normalize_topic_queue(post_cfg.get("topic_queue"))
    if not post_queue:
        post_cfg["topic_queue"] = story_queue
        post_step["config"] = post_cfg
    story_cfg["topic_queue"] = []
    story_step["config"] = story_cfg


def normalize_blocks_config(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}

    if is_v2(raw):
        steps = []
        for step in raw.get("steps") or []:
            cfg = copy.deepcopy(step.get("config") or {})
            step_type = step.get("type")
            if step_type == "post_gen":
                cfg = _normalize_post_gen_config(cfg)
            elif step_type == "story_gen":
                cfg = _normalize_story_gen_config(cfg)
            elif step_type == "tts_gen":
                cfg = _normalize_tts_gen_config(cfg)
            steps.append(
                {
                    "id": step.get("id") or _new_step_id(),
                    "type": step["type"],
                    "enabled": bool(step.get("enabled", False)),
                    "config": cfg,
                }
            )
        _migrate_story_topic_queue_into_post(steps)
        schedule = _normalize_schedule(raw.get("schedule"))
        news_rss = normalize_news_rss(raw.get("news_rss"))
        return {"version": 2, "steps": steps, "schedule": schedule, "news_rss": news_rss}

    if not isinstance(raw, dict):
        raw = {}

    steps: list[dict[str, Any]] = []
    for block_type in STEP_ORDER:
        block_data = raw.get(block_type) or {}
        enabled, config = _split_block_dict(block_type, block_data)
        if block_type == "post_gen":
            config = _normalize_post_gen_config(config)
        elif block_type == "story_gen":
            config = _normalize_story_gen_config(config)
        elif block_type == "tts_gen":
            config = _normalize_tts_gen_config(config)
        steps.append(
            {
                "id": _new_step_id(),
                "type": block_type,
                "enabled": enabled,
                "config": config,
            }
        )

    _migrate_story_topic_queue_into_post(steps)

    sched_raw = raw.get("schedule") or {}
    enabled, sched_cfg = _split_block_dict("schedule", sched_raw)
    schedule = _normalize_schedule({"enabled": enabled, **sched_cfg})

    news_raw = raw.get("news_rss") or dict(DEFAULT_NEWS_RSS)
    news_enabled, news_cfg = _split_block_dict("news_rss", news_raw)
    news_rss = normalize_news_rss({"enabled": news_enabled, **news_cfg})

    return {"version": 2, "steps": steps, "schedule": schedule, "news_rss": news_rss}


def steps_to_ui_dict(config: Any) -> dict[str, Any]:
    v2 = normalize_blocks_config(config)
    ui: dict[str, Any] = {}
    for step in v2["steps"]:
        ui[step["type"]] = {"enabled": step["enabled"], **copy.deepcopy(step["config"])}
    sched = v2.get("schedule") or {}
    ui["schedule"] = {
        "enabled": bool(sched.get("enabled", False)),
        "frequency": sched.get("frequency", "daily"),
        "times": list(sched.get("times") or []),
        "per_slot_prompts": bool(sched.get("per_slot_prompts", False)),
        "slot_prompts": dict(sched.get("slot_prompts") or {}),
    }
    ui["news_rss"] = normalize_news_rss(v2.get("news_rss"))
    return ui


def ui_dict_to_v2(ui_blocks: dict[str, Any]) -> dict[str, Any]:
    return normalize_blocks_config(ui_blocks)


def get_step_config(v2: dict[str, Any], block_type: str) -> dict[str, Any]:
    for step in v2.get("steps") or []:
        if step.get("type") == block_type:
            return {"enabled": step.get("enabled", False), **(step.get("config") or {})}
    return {}
