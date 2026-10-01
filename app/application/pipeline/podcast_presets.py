"""Default blocks config for psychology / money / biohacking / earnings podcast pipeline."""
from __future__ import annotations

from copy import deepcopy
from typing import Literal

from app.application.pipeline.normalize import normalize_blocks_config
from app.bot.states.ai_studio import DEFAULT_BLOCKS

PodcastNiche = Literal["psychology", "money", "biohacking", "earnings"]
PODCAST_NICHES = ("psychology", "money", "biohacking", "earnings")

SUNOR_NEGATIVE_TAGS = (
    "singing, rap, hip-hop, EDM, beat, drums, melody lead, chorus, "
    "instrumental solo, rhythmic vocals, dance, pop song, rock, uptempo"
)

# Shared anti-song + anti-whisper set for all spoken podcast niches.
SUNOR_NEGATIVE_TAGS_SPOKEN = (
    "whisper, soft whisper, ASMR, soft murmur, breathy voice, quiet ending, "
    "fade to whisper, soft-spoken ending, intimate whisper, sleepy narration, "
    "singing, rap, hip-hop, EDM, beat, drums, melody lead, chorus, "
    "instrumental solo, rhythmic vocals, dance, pop song, rock, uptempo, "
    "long silent gaps, meditation pacing"
)

# Back-compat alias used by older call sites / tests wording.
SUNOR_NEGATIVE_TAGS_PSYCHOLOGY = SUNOR_NEGATIVE_TAGS_SPOKEN

_NO_WHISPER = (
    "Full speaking volume from start to finish, never fade into whisper at the end, "
    "clear projected intelligible voice throughout. "
    "NO whisper, NO ASMR, NO breathy soft ending, NO soft-spoken fade-out."
)

_PSYCHOLOGY_SUNOR_TAGS = (
    "Russian podcast spoken narration only, NOT a song, no singing, no rap, no beats, "
    "no drums. Clear calm female psychologist voice, steady conversational storytelling, "
    "warm but precise diction, like a live voice message. Continuous natural flow, "
    "short natural breaths only, NO long meditation pauses. "
    f"{_NO_WHISPER} "
    "Voice clearly in front, quiet room or no music bed, about 4-5 minutes."
)

_MONEY_SUNOR_TAGS = (
    "Russian podcast spoken narration only, NOT a song, no singing, no rap, no beats, "
    "no drums. Confident clear male host voice, steady conversational pace, mentor tone, "
    "like a live voice message. Continuous natural flow, short natural breaths only, "
    "NO long meditation pauses. "
    f"{_NO_WHISPER} "
    "Voice clearly in front, quiet room or no music bed, speech always primary, "
    "about 4-5 minutes."
)

_BIOHACKING_SUNOR_TAGS = (
    "Russian podcast spoken narration only, NOT a song, no singing, no rap, no beats, "
    "no drums. Clear confident male health and biohacking expert voice, steady "
    "conversational storytelling, precise diction like a live voice message. Continuous "
    "natural flow, short natural breaths only, NO long meditation pauses. "
    f"{_NO_WHISPER} "
    "Voice clearly in front, quiet room or no music bed, about 4-5 minutes."
)

_EARNINGS_SUNOR_TAGS = (
    "Russian podcast spoken narration only, NOT a song, no singing, no rap, no beats, "
    "no drums. Energetic supportive confident male coach voice about earning ideas, "
    "motivating but practical conversational storytelling, clear diction like a live "
    "voice message. Upbeat natural flow, short natural breaths only, NO long meditation "
    "pauses. "
    f"{_NO_WHISPER} "
    "Voice clearly in front, quiet room or no music bed, about 4-5 minutes."
)

_PSYCHOLOGY_BRIEF = (
    "Канал «Психология без лишнего»: спокойный, тёплый, без воды и диагнозов. "
    "Пост — лаконичный, но по сути, с чёткими пунктами и emoji. "
    "Подкаст ~4–5 мин раскрывает ту же тему живым разговором. "
    "Без нравоучений, без клише «просто полюби себя»."
)

_MONEY_BRIEF = (
    "Канал «Деньги по делу»: уверенный, практичный тон без хайпа и «лёгких денег». "
    "Пост — лаконичный, но по сути, с чёткими пунктами и emoji. "
    "Подкаст ~4–5 мин раскрывает ту же тему как наставник. "
    "Без обещаний мгновенного богатства, без крипто-спама."
)

_BIOHACKING_BRIEF = (
    "Канал «Биохакинг на каждый день»: здоровье, энергия, сон, питание, привычки, мозг. "
    "Научно аккуратно, понятно обычному человеку, без хайпа и чудо-БАДов. "
    "Пост — лаконичный, но по сути, с чёткими пунктами и emoji. "
    "Подкаст ~4–5 мин раскрывает ту же тему как эксперт-практик."
)

_EARNINGS_BRIEF = (
    "Канал «Идеи для заработка»: практичные идеи, навыки и подходы к доходу. "
    "Энергично, с поддержкой, без хайпа «лёгких денег» и без обещаний мгновенного богатства. "
    "Пост — лаконичный, но по сути, с чёткими пунктами и emoji. "
    "Подкаст ~4–5 мин раскрывает ту же тему как мотивирующий коуч."
)

_PSYCHOLOGY_IMAGE = (
    "Спокойная психологическая иллюстрация по теме: мягкий свет, человечные образы, "
    "без текста и надписей на картинке. Квадрат 1:1."
)

_MONEY_IMAGE = (
    "Деловая, чистая иллюстрация по теме денег и решений: уверенный визуал без гламура "
    "и без текста на картинке. Квадрат 1:1."
)

_BIOHACKING_IMAGE = (
    "Чистая современная иллюстрация про здоровье и биохакинг: энергия, тело, привычки, "
    "без текста и надписей на картинке. Квадрат 1:1."
)

_EARNINGS_IMAGE = (
    "Свежая иллюстрация про идеи заработка и рост: энергия, возможности, практика, "
    "без текста и надписей на картинке. Квадрат 1:1."
)


def _niche_brief(niche: PodcastNiche) -> str:
    if niche == "money":
        return _MONEY_BRIEF
    if niche == "biohacking":
        return _BIOHACKING_BRIEF
    if niche == "earnings":
        return _EARNINGS_BRIEF
    return _PSYCHOLOGY_BRIEF


def _niche_image(niche: PodcastNiche) -> str:
    if niche == "money":
        return _MONEY_IMAGE
    if niche == "biohacking":
        return _BIOHACKING_IMAGE
    if niche == "earnings":
        return _EARNINGS_IMAGE
    return _PSYCHOLOGY_IMAGE


def _niche_sunor(niche: PodcastNiche) -> dict:
    if niche == "psychology":
        return _sunor_base(
            tags=_PSYCHOLOGY_SUNOR_TAGS,
            vocal_gender="f",
            negative_tags=SUNOR_NEGATIVE_TAGS_SPOKEN,
        )
    if niche == "biohacking":
        return _sunor_base(
            tags=_BIOHACKING_SUNOR_TAGS,
            vocal_gender="m",
            negative_tags=SUNOR_NEGATIVE_TAGS_SPOKEN,
        )
    if niche == "earnings":
        return _sunor_base(
            tags=_EARNINGS_SUNOR_TAGS,
            vocal_gender="m",
            negative_tags=SUNOR_NEGATIVE_TAGS_SPOKEN,
        )
    return _sunor_base(
        tags=_MONEY_SUNOR_TAGS,
        vocal_gender="m",
        negative_tags=SUNOR_NEGATIVE_TAGS_SPOKEN,
    )


def _sunor_base(*, tags: str, vocal_gender: str, negative_tags: str = SUNOR_NEGATIVE_TAGS) -> dict:
    return {
        "enabled": True,
        "music_mode": "custom",
        "prompt": "",
        "tags": tags,
        "negative_tags": negative_tags,
        "vocal_gender": vocal_gender,
        "make_instrumental": False,
        "generation_mode": "single",
        "pick_variant": "first_ok",
        "attach_cover_image": False,
        "extend_enabled": False,
        "prompt_source": "config",
        "target_duration_sec": 270,
        "lyrics_enabled": False,
    }


def build_podcast_blocks_ui(niche: PodcastNiche) -> dict:
    """Build AI Studio UI blocks for a podcast niche channel."""
    if niche not in PODCAST_NICHES:
        raise ValueError(f"Unknown podcast niche: {niche!r}")

    blocks = deepcopy(DEFAULT_BLOCKS)

    blocks["post_gen"].update(
        {
            "enabled": True,
            "mode": "ai",
            "user_input": _niche_brief(niche),
            "add_channel_link": False,
            "bold_headings": True,
            "use_emoji": True,
            "topic_queue": [],
            "topic_history": [],
        }
    )
    blocks["image_prompt"].update(
        {
            "enabled": True,
            "mode": "from_topic",
            "instruction": _niche_image(niche),
            "use_visual_style": True,
        }
    )
    blocks["image_gen"].update(
        {
            "enabled": True,
            "allow_text": False,
            "add_watermark": True,
            "aspect_ratio": "1:1",
            "model": "gpt-image-2",
        }
    )
    for key in ("story_gen", "tts_gen", "video_gen", "motion_fx"):
        blocks[key]["enabled"] = False
    blocks["drive_video"].update(
        {
            "enabled": False,
            "folder_id": "",
            "fixed_caption": "",
        }
    )
    blocks["news_rss"]["enabled"] = False
    blocks["sunor_gen"].update(_niche_sunor(niche))

    blocks["schedule"].update(
        {
            "enabled": True,
            "frequency": "2x_day",
            "times": [],
            "per_slot_prompts": False,
            "meditation_pipeline": False,
            "postcard_pipeline": False,
            "podcast_pipeline": True,
            "podcast_niche": niche,
            "slot_prompts": {},
            "slot_prompt_modes": {},
            "slot_image_addons": {},
            "slot_topic_queues": {},
            "slot_topic_history": {},
            "slot_topic_gen_extra": {},
            "slot_sunor_presets": {},
            "slot_image_refs": {},
        }
    )
    return blocks


def build_podcast_blocks_v2(niche: PodcastNiche) -> dict:
    return normalize_blocks_config(build_podcast_blocks_ui(niche))
