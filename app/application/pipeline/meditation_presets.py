"""Default blocks config for meditation channel pipeline."""
from __future__ import annotations

from copy import deepcopy

from app.application.pipeline.normalize import normalize_blocks_config
from app.bot.states.ai_studio import DEFAULT_BLOCKS

MORNING_UTC = "04:30"
LUNCH_UTC = "09:11"
EVENING_UTC = "15:11"

_SLOT_LABELS_MSK = {
    MORNING_UTC: "07:30",
    LUNCH_UTC: "12:11",
    EVENING_UTC: "18:11",
}


def _sunor_morning_preset() -> dict:
    return {
        "music_mode": "custom",
        "prompt": "",
        "tags": (
            "Spoken-word guided affirmation, NOT a song. Calm, warm, deep and reassuring "
            "male voice speaking in Russian. No singing, no melody, no chorus, no rhythmic "
            "vocals. Slow, clear and natural speech with meaningful pauses between "
            "affirmations. Soft cinematic ambient background, gentle piano, warm atmospheric "
            "pads, subtle evolving textures, spacious reverb. Peaceful, inspiring and slightly "
            "mystical atmosphere, creating a feeling of new opportunities, confidence and inner "
            "transformation. Very slow pace, around 50 BPM. Voice must remain the main focus. "
            "Suitable for meditation, visualization and manifestation practice."
        ),
        "negative_tags": (
            "singing, melody, chorus, rhythmic vocals, female vocals, rock, drums, "
            "loud, aggressive, uptempo, dance, pop song"
        ),
        "vocal_gender": "m",
        "make_instrumental": False,
        "generation_mode": "single",
        "slot_kind": "morning",
        "pick_variant": "first_ok",
        "attach_cover_image": False,
        "extend_enabled": False,
    }


def _sunor_lunch_preset() -> dict:
    return {
        "music_mode": "custom",
        "prompt": "",
        "tags": (
            "Russian spoken-word guided breathing exercise, NOT a song. Warm, calm, confident "
            "instructor voice, natural speech, long pauses between instructions. About 5 minutes. "
            "Very quiet atmospheric instrumental background much softer than voice: soft pads, "
            "calm piano, flute, light nature sounds. No singing, vocals as music, chorus, rap, "
            "drums, strong beat, dance rhythm. BPM max 48. Voice must stay the main focus. "
            "Safe simple coaching: inhale, exhale, hold with real time for each step. "
            "For headphones, follow-along breathing practice."
        ),
        "negative_tags": (
            "singing, melody, chorus, rhythmic vocals, rap, drums, loud beat, dance, uptempo, "
            "competition, aggressive, rock, pop song, music overpowering voice"
        ),
        "vocal_gender": "",
        "make_instrumental": False,
        "generation_mode": "single",
        "slot_kind": "lunch",
        "pick_variant": "first_ok",
        "attach_cover_image": False,
        "extend_enabled": False,
    }


def _lunch_slot_prompt() -> str:
    return (
        "Создай полноценную дыхательную практику на русском языке для аудиоформата.\n\n"
        "Это НЕ песня и НЕ музыкальная композиция. Основной элемент — спокойный человеческий "
        "голос инструктора, который мягко и понятно проводит слушателя через дыхательную "
        "практику. Голос тёплый, уверенный, естественный, без спешки, с длинными паузами "
        "между инструкциями.\n\n"
        "Продолжительность — около 5 минут.\n\n"
        "Практика должна быть безопасной, простой и понятной даже человеку без опыта. "
        "Не используй сложные дыхательные техники без необходимости. Перед началом предложи "
        "удобно сесть или лечь, расслабить плечи, закрыть глаза и обратить внимание на дыхание.\n\n"
        "Обязательно давай реальные паузы для выполнения каждого действия. Если говорится "
        "«вдох на 4 секунды», слушатель действительно должен получить время для вдоха. "
        "Не проговаривай инструкции слишком быстро.\n\n"
        "Структура:\n"
        "— короткое знакомство и настрой;\n"
        "— несколько спокойных естественных вдохов и выдохов;\n"
        "— основная дыхательная техника с понятным ритмом;\n"
        "— несколько повторений с голосовым сопровождением;\n"
        "— постепенное замедление;\n"
        "— возвращение к естественному дыханию;\n"
        "— спокойное завершение и мягкий выход из практики.\n\n"
        "Каждая практика должна иметь конкретную цель: расслабление, снятие напряжения, "
        "восстановление энергии, концентрация, утренняя настройка, дневная перезагрузка, "
        "подготовка ко сну, эмоциональное равновесие или другая подходящая цель.\n\n"
        "Подбирай дыхательный ритм индивидуально под цель практики. Не делай чрезмерно "
        "долгих задержек дыхания. Не создавай ощущение соревнования или необходимости "
        "дышать через силу.\n\n"
        "На фоне — очень тихая атмосферная инструментальная музыка, которая не мешает голосу: "
        "мягкие пады, спокойное пианино, флейта, лёгкие звуки природы, дождя, моря, леса "
        "или другие подходящие звуки. Музыка должна быть значительно тише голоса.\n\n"
        "Без вокала, пения, хора, рэпа, ударных, выраженного бита и танцевального ритма. "
        "BPM не выше 48.\n\n"
        "В конце мягко напомни слушателю сделать обычный вдох и выдох, почувствовать своё "
        "состояние, открыть глаза и вернуться к своим делам или отдыху.\n\n"
        "Текст практики гендерно нейтральный: подходит и женщинам, и мужчинам. "
        "Без «ты готова», «дорогая», «расслабилась» и других форм рода. "
        "Инструкции — императив («сделай вдох», «закрой глаза») или обращение на «вы».\n\n"
        "Главное: голосовая дыхательная практика для наушников, не песня и не просто музыка. "
        "Короткий пост для канала + полный SCRIPT для озвучки (~5 мин)."
    )




def _evening_slot_prompt() -> str:
    return (
        "Создай полноценную авторскую инструментальную медитацию.\n\n"
        "Только инструментальная музыка.\n\n"
        "Музыка должна полностью передавать атмосферу выбранной темы и создавать "
        "ощущение глубокого погружения. Слушатель должен словно оказаться внутри "
        "этого места или состояния.\n\n"
        "Используй мягкие атмосферные пады, глубокие дроны, нежное пианино, воздушные "
        "синтезаторы, мягкие струнные, флейту, поющие чаши, колокольчики и подходящие "
        "природные звуки. Инструменты подбирай индивидуально под конкретную тему.\n\n"
        "Музыка должна развиваться очень медленно и плавно, без резких переходов, "
        "неожиданных звуков и выраженной драматургии. Не используй куплеты, припевы "
        "или стандартную структуру песни.\n\n"
        "Без ударных, без танцевального ритма, без выраженного бита и энергичного грува. "
        "BPM — не выше 48, оптимально 35–45 BPM.\n\n"
        "Длинные атмосферные ноты, мягкие гармонические переходы, просторная реверберация "
        "и широкий стереозвук. Достаточно пространства и тишины между элементами.\n\n"
        "Общее ощущение: спокойствие, расслабление, гармония, тепло, глубина и внутреннее "
        "путешествие.\n\n"
        "Каждая медитация — уникальная атмосфера под тему, без повторения мелодий и "
        "структуры прошлых композиций.\n\n"
        "Напиши короткий пост для канала: опиши атмосферу и погружение по теме "
        "(без текста для озвучки — только музыка)."
    )


def _sunor_evening_preset() -> dict:
    return {
        "music_mode": "instrumental",
        "tags": (
            "Original instrumental meditation only, no vocals. Soft atmospheric pads, deep "
            "drones, gentle piano, airy synths, soft strings, flute, singing bowls, bells, "
            "nature textures matched to theme. Deep immersive atmosphere, listener inside the "
            "place or state. Very slow smooth evolution, no abrupt changes, no song structure. "
            "No drums, dance beat, strong groove. BPM 35-45, max 48. Long sustained notes, "
            "soft harmonic shifts, spacious reverb, wide stereo, silence between layers. "
            "Calm relaxation harmony warmth depth inner journey. Unique mood per theme."
        ),
        "negative_tags": (
            "vocals, singing, choir, rap, drums, percussion, beat, dance, groove, rock, "
            "sudden drops, verse chorus, uptempo, aggressive, pop song, EDM"
        ),
        "make_instrumental": True,
        "generation_mode": "merge_4tracks",
        "merge_crossfade_ms": 5000,
        "slot_kind": "evening",
        "pick_variant": "first",
        "attach_cover_image": False,
        "extend_enabled": False,
    }


def build_meditation_blocks_ui() -> dict:
    blocks = deepcopy(DEFAULT_BLOCKS)

    blocks["post_gen"].update(
        {
            "enabled": True,
            "mode": "ai",
            "user_input": (
                "Медитационный канал: тёплый дружелюбный тон, emoji, "
                "первая строка с «уже здесь»."
            ),
            "add_channel_link": True,
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
            "instruction": (
                "Сгенерируй спокойную медitative иллюстрацию по теме. "
                "Горизонтальная композиция 16:9, widescreen. "
                "Пастельные тона, мягкий свет, без текста и надписей."
            ),
            "use_visual_style": True,
        }
    )
    blocks["image_gen"].update(
        {
            "enabled": True,
            "allow_text": False,
            "add_watermark": True,
            "aspect_ratio": "16:9",
        }
    )
    blocks["sunor_gen"].update(
        {
            "enabled": True,
            "music_mode": "custom",
            "pick_variant": "first_ok",
            "attach_cover_image": False,
        }
    )
    blocks["schedule"].update(
        {
            "enabled": True,
            "frequency": "daily",
            "times": [MORNING_UTC, LUNCH_UTC, EVENING_UTC],
            "per_slot_prompts": True,
            "meditation_pipeline": True,
            "podcast_pipeline": False,
            "slot_prompts": {
                MORNING_UTC: (
                    "Утренняя spoken-word аффirmация на русском: материализация мыслей, "
                    "уверенность, новые возможности. Мужской спокойный голос, не песня — "
                    "короткий пост + текст для озвучки 2–3 мин с паузами между фразами. "
                    "Текст гендерно нейтральный: без «я готова/спокоен», «дорогая»; "
                    "аффирмации — «я принимаю», «во мне сила»."
                ),
                LUNCH_UTC: _lunch_slot_prompt(),
                EVENING_UTC: _evening_slot_prompt(),
            },
            "slot_image_addons": {
                MORNING_UTC: "рассвет, мягкий утренний свет",
                LUNCH_UTC: "день, спокойствие, природа",
                EVENING_UTC: "луна, звёзды, глубокий покой",
            },
            "slot_topic_queues": {MORNING_UTC: [], LUNCH_UTC: [], EVENING_UTC: []},
            "slot_topic_history": {MORNING_UTC: [], LUNCH_UTC: [], EVENING_UTC: []},
            "slot_topic_gen_extra": {
                MORNING_UTC: (
                    "темы аффirmаций и материализации мыслей, без повторов, "
                    "без привязки к полу"
                ),
                LUNCH_UTC: (
                    "типы дыхательных практик: 4-7-8, box breathing, живот, "
                    "без привязки к полу"
                ),
                EVENING_UTC: (
                    "темы медитаций: сон, расслабление, исцеление, чакры, "
                    "без привязки к полу"
                ),
            },
            "slot_sunor_presets": {
                MORNING_UTC: _sunor_morning_preset(),
                LUNCH_UTC: _sunor_lunch_preset(),
                EVENING_UTC: _sunor_evening_preset(),
            },
            "slot_image_refs": {},
        }
    )
    return blocks


def build_meditation_blocks_v2() -> dict:
    return normalize_blocks_config(build_meditation_blocks_ui())


def slot_msk_label(slot_time_utc: str) -> str:
    return _SLOT_LABELS_MSK.get(str(slot_time_utc).strip(), str(slot_time_utc))
