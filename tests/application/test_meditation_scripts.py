"""Meditation GPT content contract."""

import pytest

from app.application.pipeline.meditation_post_budget import POST_MAX_PUBLISHED
from app.application.pipeline.meditation_scripts import (
    AUDIO_SCRIPT_MAX_CHARS,
    POST_MAX_CHARS,
    _parse_post_script,
    generate_meditation_content,
    truncate_post,
)


class _OAI:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt = ""
        self.last_system = ""

    async def generate_text(self, prompt, system_prompt=None, model=None):
        self.last_prompt = prompt
        self.last_system = system_prompt or ""
        return self.response


@pytest.mark.asyncio
async def test_evening_post_only_no_script():
    oai = _OAI("🕯️ Медитация «Лунный свет» уже здесь.\n\nСпокойный вечер 🌙")
    post, script, title = await generate_meditation_content(
        oai,
        brief="вечерний ambient",
        channel_title="Медитации",
        topic="Лунный свет",
        slot_kind="evening",
    )
    assert title == "Лунный свет"
    assert script == ""
    assert "Лунн" in post
    assert len(post) <= POST_MAX_CHARS
    assert "уже здесь" in oai.last_prompt.lower()
    assert "обязательно" in oai.last_prompt.lower() or "ОБЯЗАТЕЛЬНО" in oai.last_prompt


@pytest.mark.asyncio
async def test_evening_truncates_long_post():
    long_post = "🕯️ Медитация «Тема» уже здесь.\n\n" + ("Тихий покой. " * 80)
    oai = _OAI(long_post)
    post, script, title = await generate_meditation_content(
        oai,
        brief="вечер",
        channel_title="Медитации",
        topic="Тема",
        slot_kind="evening",
        max_post_chars=400,
    )
    assert title == "Тема"
    assert script == ""
    assert len(post) <= 400


@pytest.mark.asyncio
async def test_morning_parses_post_and_script():
    raw = (
        "POST:\n✨ Материализация мыслей уже здесь!\n\n"
        "Сегодня — практика «Сила мысли» 🚪\n\n"
        "SCRIPT:\nЯ принимаю изобилие. Я материализую свои мысли."
    )
    oai = _OAI(raw)
    post, script, title = await generate_meditation_content(
        oai,
        brief="утренние аффirmации",
        channel_title="Медитации",
        topic="Сила мысли",
        slot_kind="morning",
        max_post_chars=600,
    )
    assert title == "Сила мысли"
    assert "уже здесь" in post.lower()
    assert "принимаю" in script.lower()
    assert len(script) <= AUDIO_SCRIPT_MAX_CHARS
    assert len(post) <= 600
    assert "уже здесь" in oai.last_prompt


@pytest.mark.asyncio
async def test_lunch_opening_hint_in_prompt():
    oai = _OAI(
        "POST:\n🌿 Дыхательная практика «Отдых» уже здесь.\n\n"
        "SCRIPT:\nВдох…"
    )
    await generate_meditation_content(
        oai,
        brief="дыхание",
        channel_title="Медитации",
        topic="Отдых",
        slot_kind="lunch",
    )
    assert "Дыхательная практика «Отдых» уже здесь" in oai.last_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("slot_kind", ["morning", "lunch", "evening"])
async def test_prompts_are_gender_neutral(slot_kind: str):
    oai = _OAI(
        "POST:\n✨ Практика уже здесь!\n\nSCRIPT:\nЯ принимаю спокойствие."
    )
    await generate_meditation_content(
        oai,
        brief="практика",
        channel_title="Медитации",
        topic="Спокойствие",
        slot_kind=slot_kind,
    )
    combined = f"{oai.last_system}\n{oai.last_prompt}".lower()
    assert "подруге" not in combined
    assert "женской аудитории" not in combined
    assert "гендерно нейтральн" in combined
    assert "без привязки к полу" in combined or "подходят и женщинам, и мужчинам" in combined


def test_parse_post_script_blocks():
    raw = "POST:\nHello\n\nSCRIPT:\nSpeak this"
    post, script = _parse_post_script(raw)
    assert post == "Hello"
    assert script == "Speak this"


def test_parse_post_script_fallback():
    post, script = _parse_post_script("Single block text")
    assert post == "Single block text"
    assert script == ""


def test_truncate_post_prefers_sentence_boundary():
    text = "А" * 400 + ". " + "Б" * 500
    out = truncate_post(text, max_chars=POST_MAX_PUBLISHED)
    assert len(out) <= POST_MAX_PUBLISHED
    assert out.endswith(".") or out.endswith("…")


def test_truncate_post_short_unchanged():
    text = "Короткий пост."
    assert truncate_post(text) == text


def test_post_max_matches_published():
    assert POST_MAX_CHARS == POST_MAX_PUBLISHED == 800
