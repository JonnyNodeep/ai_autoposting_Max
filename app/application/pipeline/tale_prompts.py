"""Fixed fairy-tale scenario prompts: lullaby / bedtime / age 3-5 (label 3–6)."""
from __future__ import annotations

from typing import Final

LANDSCAPE_IMAGE_SUFFIX: Final[str] = (
    "Wide horizontal landscape composition, 16:9 cinematic framing, "
    "important subjects centered, no vertical portrait layout. "
    "Horizontal widescreen storybook illustration, no text, no letters."
)

# Fixed Studio pipeline scenario (no UI picker).
FIXED_TALE_STYLE: Final[str] = "lullaby"
FIXED_TALE_MOOD: Final[str] = "bedtime"
FIXED_TALE_AGE: Final[str] = "3-5"
FIXED_TALE_AGE_LABEL: Final[str] = "3–6 лет"

STORY_TARGET_CHARS = 4500
SCENES_MIN = 6
SCENES_MAX = 8

SUNOR_BASE_TAGS = (
    "children's bedtime story, spoken narration only, NOT a song, "
    "warm gentle adult narrator, calm and sleepy voice, clear pronunciation, "
    "slow storytelling with natural pauses, "
    "absolutely NO singing, NO melody, NO chanting, NO rap"
)

SUNOR_NEGATIVE_TAGS = (
    "singing, sung vocals, melody, melodic lead vocal, chanting, rap, "
    "drums, percussion, strong rhythm, beat-driven, "
    "pop chorus, screaming, heavy drums, EDM, loud mix, karaoke, anthem"
)

_STYLE_BGM = (
    "very quiet background music, soft piano, delicate music-box notes, "
    "warm ambient pads, subtle magical forest atmosphere, "
    "music far behind the voice, never cover the narration, "
    "no drums, no percussion, no strong rhythm"
)

_STYLE_IMAGE = (
    "sleepy pastel night nursery illustration, low contrast, cozy moonlight, "
    "calm bedtime mood, no text, no letters"
)

_STYLE_STORY_RU = (
    "Убаюкивающая сказка: минимум конфликта, максимум тепла и покоя, "
    "финал тише и соннее, чтобы слушатель засыпал."
)

_MOOD_STORY_RU = (
    "Настроение: колыбельная / на ночь. Финал должен становиться тише и спокойнее."
)

_MOOD_BGM = (
    "cozy safe peaceful bedtime atmosphere for children aged 3-6, "
    "slow pace, soft dynamics, gentle transitions, "
    "ending quieter and more relaxing, helping the child fall asleep"
)

_AGE_STORY_RU = (
    "Возраст 3–6 лет: очень короткие предложения, конкретные образы, ноль страха, "
    "ноль жести, ноль сложных тем; мир абсолютно безопасный."
)


def build_sunor_tags(
    style: str = FIXED_TALE_STYLE,
    mood: str = FIXED_TALE_MOOD,
    age: str = FIXED_TALE_AGE,
) -> str:
    _ = (style, mood, age)  # fixed scenario; params kept for call-site clarity
    parts = [
        SUNOR_BASE_TAGS,
        _STYLE_BGM,
        _MOOD_BGM,
    ]
    return ", ".join(p.strip().rstrip(",") for p in parts if p.strip())


def build_sunor_negative_tags() -> str:
    return SUNOR_NEGATIVE_TAGS


def build_image_style_prefix(style: str = FIXED_TALE_STYLE) -> str:
    _ = style
    return _STYLE_IMAGE


def wrap_story_for_sunor(story: str) -> str:
    body = (story or "").strip()
    return (
        "[Children's bedtime story — spoken narration only, NOT a song]\n"
        "[Very quiet background music far behind voice]\n\n"
        f"{body}\n\n"
        "[Ending — quieter, slower, more relaxing, fading to sleep]"
    )


def build_story_system_prompt(
    *,
    style: str = FIXED_TALE_STYLE,
    mood: str = FIXED_TALE_MOOD,
    age: str = FIXED_TALE_AGE,
) -> str:
    _ = (style, mood, age)
    return (
        "Ты — автор аудиосказок для озвучки (spoken narration) и раскадровки.\n"
        "Отвечай ТОЛЬКО валидным JSON-объектом без markdown:\n"
        '{"title":"...","caption":"...","story":"...","scenes":[{"id":1,'
        '"story_span":"...","image_prompt_en":"...","hero_in_scene":false}]}.\n'
        "title — короткое название сказки.\n"
        "caption — анонс на 2–4 предложения.\n"
        "В caption НЕ пиши призывы подписаться или поделиться — это добавит система.\n"
        "story — полный текст для озвучки: чистая русская проза, без markdown, "
        "без эмодзи, без [Verse]/[Chorus], без CTA.\n"
        "story — цельный рассказ: завязка, развитие и полный финал; "
        "не обрывай на середине. Если не влезаешь в лимит — сожми сюжет, "
        "убери второстепенные детали, но сохрани логичную концовку.\n"
        f"Длина story: не более {STORY_TARGET_CHARS} символов "
        "(жёсткий максимум, не превышай).\n"
        f"scenes: от {SCENES_MIN} до {SCENES_MAX} сцен по порядку.\n"
        "Каждый story_span — непрерывный фрагмент story; вместе spans покрывают "
        "весь story без дыр и без сильных пересечений.\n"
        "image_prompt_en — детальный ENGLISH prompt одной иллюстрации сцены, "
        "без текста/букв на картинке, wholesome, safe.\n"
        "image_prompt_en — wide horizontal landscape composition (16:9), "
        "subjects centered, no vertical/portrait layout.\n"
        f"{_STYLE_STORY_RU}\n"
        f"{_MOOD_STORY_RU}\n"
        f"{_AGE_STORY_RU}\n"
        "Соблюдай возрастной safety. Язык story и caption: русский."
    )


def build_story_user_prompt(
    *,
    topic: str,
    style: str = FIXED_TALE_STYLE,
    mood: str = FIXED_TALE_MOOD,
    age: str = FIXED_TALE_AGE,
) -> str:
    _ = (style, mood, age)
    brief = (topic or "").strip()[:3500] or "Добрая сказка"
    return (
        f"Тема / бриф:\n«{brief}»\n\n"
        f"Стиль: Убаюкивающая (lullaby)\n"
        f"Настроение: На ночь (bedtime)\n"
        f"Возраст: {FIXED_TALE_AGE_LABEL} ({FIXED_TALE_AGE})\n\n"
        f"story: не длиннее {STORY_TARGET_CHARS} символов — цельная сказка с финалом.\n"
        "Напиши оригинальную сказку и раскадровку scenes."
    )


def build_story_shorten_user_prompt(
    *,
    topic: str,
    title: str,
    story_len: int,
    max_chars: int = STORY_TARGET_CHARS,
) -> str:
    brief = (topic or "").strip()[:3500] or "Добрая сказка"
    return (
        f"Первая версия сказки получилась слишком длинной ({story_len} символов).\n"
        f"Перепиши сказку и scenes заново: story — не более {max_chars} символов.\n\n"
        "Важно:\n"
        "• сохрани тему и главного героя;\n"
        "• сказка должна быть цельной — завязка, развитие и полный финал;\n"
        "• не обрывай на середине; если нужно — сожми сюжет, убери лишнее;\n"
        "• ответь полным JSON: title, caption, story, scenes.\n\n"
        f"Тема / бриф: «{brief}»\n"
        f"Было title: {title or '—'}"
    )


def finalize_scene_image_prompt(
    image_prompt_en: str, *, style: str = FIXED_TALE_STYLE
) -> str:
    base = (image_prompt_en or "").strip()
    prefix = build_image_style_prefix(style)
    if not base:
        return f"{prefix}. Wholesome storybook scene. {LANDSCAPE_IMAGE_SUFFIX}"
    if prefix.lower()[:40] in base.lower():
        return f"{base}. {LANDSCAPE_IMAGE_SUFFIX}"
    return f"{prefix}. Scene: {base}. {LANDSCAPE_IMAGE_SUFFIX}"
