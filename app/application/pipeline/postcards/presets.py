"""Default blocks config for Living Postcards channel pipeline."""
from __future__ import annotations

from copy import deepcopy

from app.application.pipeline.normalize import normalize_blocks_config
from app.application.pipeline.postcards.topics import (
    DAILY_UTC,
    EVENING_UTC,
    MORNING_UTC,
    OFFICIAL_UTC,
)
from app.bot.states.ai_studio import DEFAULT_BLOCKS

_SLOT_LABELS_MSK = {
    MORNING_UTC: "07:23",
    OFFICIAL_UTC: "11:37",
    DAILY_UTC: "12:37",
    EVENING_UTC: "19:19",
}

_BASE_POST_BRIEF = (
    "Тёплый канал живых открыток. Напиши короткое поздравление в виде стиха "
    "на русском (максимум 350 символов). Без призывов подписаться, без ссылок, "
    "без хэштегов и CTA. Только стих."
)


def build_postcard_blocks_ui() -> dict:
    blocks = deepcopy(DEFAULT_BLOCKS)

    blocks["post_gen"].update(
        {
            "enabled": True,
            "mode": "ai",
            "user_input": _BASE_POST_BRIEF,
            "add_channel_link": False,
            "bold_headings": False,
            "use_emoji": True,
            "topic_queue": [],
            "topic_history": [],
        }
    )
    blocks["image_prompt"].update(
        {
            "enabled": True,
            "mode": "from_topic",
            "instruction": (
                "Премиальная живая открытка, которую хочется переслать: дорогая "
                "иллюстрация, мягкий свет, высокое качество. Сцена строго по теме. "
                "Можно лица людей. Животных и питомцев — только если тема прямо "
                "про них; не добавляй кошек как случайный милый декор. "
                "На картинке обязательно напиши кириллицей красивым понятным "
                "читаемым шрифтом короткую поздравительную фразу по теме."
            ),
            "use_visual_style": False,
        }
    )
    blocks["image_gen"].update(
        {
            "enabled": True,
            "allow_text": True,
            "add_watermark": False,
            "aspect_ratio": "1:1",
        }
    )
    blocks["video_gen"].update({"enabled": False})
    blocks["motion_fx"] = {
        "enabled": True,
        "duration_s": 4,
        "preset_group": "auto",
    }
    blocks["story_gen"].update({"enabled": False})
    blocks["tts_gen"].update({"enabled": False})
    blocks["sunor_gen"].update({"enabled": False})
    blocks["drive_video"] = dict(blocks.get("drive_video") or {"enabled": False})
    blocks["drive_video"]["enabled"] = False
    blocks["news_rss"] = dict(blocks.get("news_rss") or {"enabled": False})
    blocks["news_rss"]["enabled"] = False

    blocks["schedule"].update(
        {
            "enabled": True,
            "frequency": "daily",
            "times": [MORNING_UTC, OFFICIAL_UTC, DAILY_UTC, EVENING_UTC],
            "per_slot_prompts": True,
            "meditation_pipeline": False,
            "postcard_pipeline": True,
            "podcast_pipeline": False,
            "slot_prompts": {
                MORNING_UTC: (
                    "Утренний слот: доброе утро, любовь, семья, дружба, мотивация "
                    "или сезонное настроение. Стих-поздравление до 350 символов."
                ),
                OFFICIAL_UTC: (
                    "Дневной слот официальных/крупных праздников России. "
                    "Если сегодня нет официальной даты — общее поздравление "
                    "с днём рождения. Стих до 350 символов."
                ),
                DAILY_UTC: (
                    "Дневной слот профессиональных, народных и памятных дат. "
                    "Стих-поздравление до 350 символов."
                ),
                EVENING_UTC: (
                    "Вечерний слот: спокойной ночи, пожелания любви и тепла, "
                    "семейные и сезонные вечерние сюжеты. Стих до 350 символов."
                ),
            },
            "slot_image_addons": {
                MORNING_UTC: "утренний свет, свежесть, тёплая открытка",
                OFFICIAL_UTC: "праздничная торжественная открытка, праздничный декор",
                DAILY_UTC: "профессиональный или тематический праздник, красивая открытка",
                EVENING_UTC: "вечерний мягкий свет, уют, спокойствие",
            },
            "slot_topic_queues": {
                MORNING_UTC: [],
                EVENING_UTC: [],
            },
            "slot_topic_history": {
                MORNING_UTC: [],
                EVENING_UTC: [],
            },
            "slot_topic_gen_extra": {
                MORNING_UTC: (
                    "темы утренних открыток: доброе утро, любовь, семья, дружба, "
                    "мотивация, сезонное настроение; короткие формулировки"
                ),
                EVENING_UTC: (
                    "темы вечерних открыток: спокойной ночи, любовь и тепло, "
                    "семья, сезонные вечерние сюжеты; короткие формулировки"
                ),
            },
            "slot_sunor_presets": {},
            "slot_image_refs": {},
        }
    )
    return blocks


def build_postcard_blocks_v2() -> dict:
    return normalize_blocks_config(build_postcard_blocks_ui())


def slot_msk_label(slot_time_utc: str) -> str:
    return _SLOT_LABELS_MSK.get(str(slot_time_utc).strip(), str(slot_time_utc))
