import pytest

from app.application.pipeline.generate_post import (
    MAX_TOPIC_ATTEMPTS,
    TopicDedupExhausted,
    generate_post_text,
    _is_topic_duplicate,
)
from app.application.pipeline.recent_topics import (
    fetch_recent_post_topics,
    topic_from_post_text,
    topics_from_messages,
)


def test_topic_from_post_text_first_line():
    text = "🍲 Гречка с индейкой\n\nДлинный текст рецепта"
    assert topic_from_post_text(text) == "🍲 Гречка с индейкой"


def test_topic_from_post_text_truncates():
    long = "A" * 200
    out = topic_from_post_text(long, max_len=50)
    assert len(out) <= 50
    assert out.endswith("…")


def test_topics_from_messages_dedupes():
    messages = [
        {"body": {"text": "Салат с тунцом\nтекст"}},
        {"body": {"text": ""}},
        {"body": {"text": "салат с тунцом\nдругой"}},
        {"body": {"text": "Овсянка с ягодами"}},
    ]
    assert topics_from_messages(messages) == ["Салат с тунцом", "Овсянка с ягодами"]


@pytest.mark.asyncio
async def test_fetch_recent_post_topics_ok():
    class _Max:
        async def get_messages(self, chat_id, count=50):
            assert chat_id == 42
            return [{"body": {"text": "Тема A\nbody"}}]

    topics = await fetch_recent_post_topics(_Max(), 42)
    assert topics == ["Тема A"]


@pytest.mark.asyncio
async def test_fetch_recent_post_topics_api_error_returns_empty():
    class _Max:
        async def get_messages(self, chat_id, count=50):
            raise RuntimeError("boom")

    assert await fetch_recent_post_topics(_Max(), 1) == []


class _ScriptedOAI:
    """Returns scripted replies in order; records all prompts."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []
        self.calls = 0

    async def generate_text(self, prompt: str, system_prompt: str = "") -> str:
        self.calls += 1
        self.prompts.append(prompt)
        if not self._replies:
            raise AssertionError(f"unexpected generate_text call #{self.calls}")
        return self._replies.pop(0)


@pytest.mark.asyncio
async def test_generate_post_text_includes_recent_topics():
    oai = _ScriptedOAI(
        [
            "Уникальная тема про киноа",
            "OK",
            "**Уникальный пост**\nтекст",
        ]
    )
    await generate_post_text(
        oai,
        "бриф",
        "Канал",
        recent_topics=["Гречка с индейкой", "Салат с тунцом"],
    )
    propose_prompt = oai.prompts[0]
    write_prompt = oai.prompts[2]
    assert "Гречка с индейкой" in propose_prompt
    assert "Салат с тунцом" in propose_prompt
    assert "НЕ повторяй" in propose_prompt
    assert "Уникальная тема про киноа" in write_prompt
    assert oai.calls == 3  # propose + judge + write


@pytest.mark.asyncio
async def test_generate_post_empty_topics_skips_judge():
    oai = _ScriptedOAI(["просто пост"])
    result, topic = await generate_post_text(oai, "бриф", "Канал", recent_topics=[])
    assert result == "просто пост"
    assert topic == "просто пост"
    assert oai.calls == 1


@pytest.mark.asyncio
async def test_generate_post_approved_topic_skips_propose_and_judge():
    oai = _ScriptedOAI(["**Пост по теме**\nтело"])
    result, topic = await generate_post_text(
        oai,
        "бриф",
        "Канал",
        recent_topics=["Старая тема"],
        approved_topic="Свет в гостиной",
    )
    assert "Пост по теме" in result
    assert topic == "Свет в гостиной"
    assert oai.calls == 1
    assert "Свет в гостиной" in oai.prompts[0]
    assert "Старая тема" in oai.prompts[0]


@pytest.mark.asyncio
async def test_generate_post_retries_on_duplicate_then_ok():
    oai = _ScriptedOAI(
        [
            "Зеркала в интерьере",
            "DUPLICATE",
            "Свет и тени в гостиной",
            "OK",
            "**Свет и тени в гостиной**\nтекст2",
        ]
    )
    result, topic = await generate_post_text(
        oai,
        "бриф",
        "Интерьер",
        recent_topics=["🪞 Зеркала, которые делают комнату больше"],
    )
    assert "Свет и тени" in result
    assert topic == "Свет и тени в гостиной"
    assert oai.calls == 5  # propose, judge, propose, judge, write
    # Second propose includes all rejected topics
    assert "Зеркала в интерьере" in oai.prompts[2]
    assert "отвергнуты" in oai.prompts[2].lower()
    assert "Свет и тени в гостиной" in oai.prompts[4]


@pytest.mark.asyncio
async def test_generate_post_raises_after_exhausted_retries(monkeypatch):
    monkeypatch.setattr(
        "app.application.pipeline.generate_post.MAX_TOPIC_ATTEMPTS",
        2,
    )
    oai = _ScriptedOAI(
        [
            "Ребёнок липнет к маме",
            "DUPLICATE",
            "Прилипчивый ребёнок вечером",
            "DUPLICATE",
        ]
    )
    with pytest.raises(TopicDedupExhausted) as ei:
        await generate_post_text(
            oai,
            "бриф",
            "Дочки",
            recent_topics=["Почему ребёнок становится прилипчивым вечером"],
        )
    assert ei.value.attempts == 2
    assert "Ребёнок липнет к маме" in ei.value.rejected_topics
    assert "Прилипчивый ребёнок вечером" in ei.value.rejected_topics
    assert oai.calls == 4  # no write call


@pytest.mark.asyncio
async def test_generate_post_judge_error_fail_open():
    class _BoomJudge(_ScriptedOAI):
        async def generate_text(self, prompt: str, system_prompt: str = "") -> str:
            self.calls += 1
            self.prompts.append(prompt)
            if "DUPLICATE или OK" in prompt or "только DUPLICATE" in prompt:
                raise RuntimeError("judge down")
            if not self._replies:
                raise AssertionError("no replies")
            return self._replies.pop(0)

    oai = _BoomJudge(["Новая тема", "**Новый пост**\nтело"])
    result, topic = await generate_post_text(
        oai,
        "бриф",
        "Канал",
        recent_topics=["Старая тема"],
    )
    assert "Новый пост" in result
    assert topic == "Новая тема"
    assert oai.calls == 3  # propose + failed judge (fail-open) + write


@pytest.mark.asyncio
async def test_generate_news_skips_judge():
    oai = _ScriptedOAI(["новостной пост"])
    result, topic = await generate_post_text(
        oai,
        "бриф",
        "Канал",
        recent_topics=["Старая тема"],
        news_item={"title": "Новость", "summary": "факт", "url": "https://x.test"},
    )
    assert result == "новостной пост"
    assert topic == "Новость"
    assert oai.calls == 1


@pytest.mark.asyncio
async def test_generate_news_includes_brief_and_style_profile():
    oai = _ScriptedOAI(["готовый новостной пост"])
    result, topic = await generate_post_text(
        oai,
        "дружелюбный тон, без желтухи",
        "Хороший Екатеринбург",
        news_item={
            "title": "Открыли парк",
            "summary": "В центре открыли новый парк",
            "url": "https://e1.ru/x",
        },
        style_profile={
            "tone": "тёплый и позитивный",
            "audience": "жители Екатеринбурга",
            "custom_prompt": "короткие абзацы",
        },
    )
    assert result == "готовый новостной пост"
    assert topic == "Открыли парк"
    prompt = oai.prompts[0]
    assert "Открыли парк" in prompt
    assert "тёплый и позитивный" in prompt
    assert "жители Екатеринбурга" in prompt
    assert "короткие абзацы" in prompt
    assert "дружелюбный тон, без желтухи" in prompt
    assert "Редакционные правила" in prompt
    assert "Стиль канала" in prompt
    assert "приоритет выше стиля" in prompt or "единственный источник правды" in prompt


@pytest.mark.asyncio
async def test_is_topic_duplicate_parses_verdict():
    assert await _is_topic_duplicate(_ScriptedOAI(["DUPLICATE"]), "A", ["B"]) is True
    assert await _is_topic_duplicate(_ScriptedOAI(["OK"]), "A", ["B"]) is False
    assert await _is_topic_duplicate(_ScriptedOAI(["duplicate."]), "A", ["B"]) is True
    assert await _is_topic_duplicate(_ScriptedOAI(["maybe"]), "A", ["B"]) is False


def test_max_topic_attempts_default():
    assert MAX_TOPIC_ATTEMPTS == 15
