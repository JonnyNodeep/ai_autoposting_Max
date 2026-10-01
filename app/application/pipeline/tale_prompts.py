"""Fixed fairy-tale scenario prompts: lullaby / bedtime / age 3-5 (label 3–6)."""
from __future__ import annotations

from typing import Final

LANDSCAPE_IMAGE_SUFFIX: Final[str] = (
    "Wide horizontal landscape composition, 16:9 cinematic framing, "
    "important subjects centered, no vertical portrait layout. "
    "Horizontal widescreen storybook illustration, no text, no letters."
)

IMAGE_CONTINUITY_SUFFIX: Final[str] = (
    "Same art style, same character design, same color palette, "
    "same lighting language, consecutive story frames of one film, "
    "do not change the hero's species, colors, age, or clothes between frames."
)

# Fixed Studio pipeline scenario (no UI picker).
FIXED_TALE_STYLE: Final[str] = "lullaby"
FIXED_TALE_MOOD: Final[str] = "bedtime"
FIXED_TALE_AGE: Final[str] = "3-5"
FIXED_TALE_AGE_LABEL: Final[str] = "3–6 лет"

STORY_TARGET_CHARS = 4500
SCENES_MIN = 10
SCENES_MAX = 12

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
    "sleepy pastel night nursery storybook illustration, low contrast, "
    "cozy moonlight, calm bedtime mood, consistent character design and "
    "palette across every frame of one film, no text, no letters"
)

_STORY_CRAFT_RU = (
    "Настроение: колыбельная на ночь. Напиши настоящую маленькую сказку, "
    "не описание настроения.\n"
    "Структура: герой → простое желание → мягкая заминка → доброе решение → сон. "
    "Середина может быть чуть живее; только финал ведёт к засыпанию.\n"
    "Покой показывай ритмом и образами (свет, глазки, одеялко, постель) — "
    "не словами про громкость.\n"
    "Антиштамп: слова «тихо», «тише», «тихонько», «шёпот» — не чаще 1–2 раз "
    "за всю сказку; не раздувай «мягко», «спокойно», «уютно».\n"
    "Конкретика: имя героя, предмет, 1–2 сенсорные детали (звук, цвет, запах). "
    "Добавь 2–4 короткие реплики.\n"
    "Ритм для озвучки: чередуй короткие и средние фразы; не строчи рубленые "
    "однотипные предложения подряд.\n"
    "Не начинай каждую сказку с «Жил-был… ночью… луна…» и не заканчивай "
    "только «Доброй ночи». Варьируй зачин и финал.\n"
    "Если не влезаешь в лимит — сначала сохрани полный финал, потом убери "
    "украшения и второстепенные детали."
)

_MOOD_BGM = (
    "cozy safe peaceful bedtime atmosphere for children aged 3-6, "
    "slow pace, soft dynamics, gentle transitions, "
    "ending quieter and more relaxing, helping the child fall asleep"
)

_AGE_STORY_RU = (
    "Возраст 3–6 лет: простые слова, конкретные образы, короткие и средние "
    "фразы вперемешку; ноль страха, ноль жести, ноль сложных тем; "
    "мир абсолютно безопасный."
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
        '{"title":"...","caption":"...","story":"...","visual_lock":"...",'
        '"scenes":[{"id":1,"story_span":"...","image_prompt_en":"...",'
        '"hero_in_scene":false}]}.\n'
        "title — короткое название сказки.\n"
        "caption — анонс на 2–4 предложения.\n"
        "В caption НЕ пиши призывы подписаться или поделиться — это добавит система.\n"
        "story — полный текст для озвучки: чистая русская проза, без markdown, "
        "без эмодзи, без [Verse]/[Chorus], без CTA.\n"
        "story — цельный рассказ: завязка, развитие и полный финал; "
        "не обрывай на середине.\n"
        f"Длина story: не более {STORY_TARGET_CHARS} символов "
        "(жёсткий максимум, не превышай).\n"
        "visual_lock — обязательный ENGLISH якорь на 1–3 предложения: вид героя "
        "(species/colors/clothes), техника иллюстрации, палитра и lighting; "
        "одинаковый для всех кадров одного фильма.\n"
        f"scenes: от {SCENES_MIN} до {SCENES_MAX} сцен — биты сюжета по порядку "
        "(не равные куски текста). На всю сказку 1–2 локации; не прыгай фоном "
        "без нужды.\n"
        "Каждый story_span — непрерывный фрагмент story; вместе spans покрывают "
        "весь story без дыр и без сильных пересечений.\n"
        "image_prompt_en — ENGLISH: начни с дословного visual_lock, затем только "
        "место/поза/действие этой сцены; без текста/букв на картинке, wholesome, safe.\n"
        "Кадры одного фильма: не меняй породу, цвет, возраст и одежду героя.\n"
        "hero_in_scene: true — в image_prompt_en явно тот же герой из visual_lock.\n"
        "image_prompt_en — wide horizontal landscape composition (16:9), "
        "subjects centered, no vertical/portrait layout.\n"
        f"{_STORY_CRAFT_RU}\n"
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
        f"scenes: {SCENES_MIN}–{SCENES_MAX} битов сюжета + visual_lock.\n"
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
        "• сначала полный финал (сон), потом убери украшения;\n"
        "• сказка цельная: герой → желание → заминка → решение → сон;\n"
        "• не заливай текст словами «тихо/тише/тихонько/шёпот»;\n"
        "• сохрани visual_lock и согласованные scenes;\n"
        "• ответь полным JSON: title, caption, story, visual_lock, scenes.\n\n"
        f"Тема / бриф: «{brief}»\n"
        f"Было title: {title or '—'}"
    )


def finalize_scene_image_prompt(
    image_prompt_en: str,
    *,
    style: str = FIXED_TALE_STYLE,
    visual_lock: str = "",
) -> str:
    base = (image_prompt_en or "").strip()
    lock = (visual_lock or "").strip()
    prefix = build_image_style_prefix(style)
    base_l = base.lower()

    head: list[str] = []
    if not base or prefix.lower()[:40] not in base_l:
        head.append(prefix)
    if lock and lock.lower() not in base_l:
        head.append(lock)

    if base:
        scene = base if (not head or prefix.lower()[:40] in base_l) else f"Scene: {base}"
    else:
        scene = "Wholesome storybook scene"

    body = ". ".join([*head, scene])
    return f"{body}. {IMAGE_CONTINUITY_SUFFIX} {LANDSCAPE_IMAGE_SUFFIX}"
