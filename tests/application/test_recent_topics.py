import pytest

from app.application.pipeline.generate_post import generate_post_text
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


@pytest.mark.asyncio
async def test_generate_post_text_includes_recent_topics():
    class _OAI:
        def __init__(self) -> None:
            self.prompt = ""

        async def generate_text(self, prompt: str, system_prompt: str = "") -> str:
            self.prompt = prompt
            return "пост"

    oai = _OAI()
    await generate_post_text(
        oai,
        "бриф",
        "Канал",
        recent_topics=["Гречка с индейкой", "Салат с тунцом"],
    )
    assert "Гречка с индейкой" in oai.prompt
    assert "Салат с тунцом" in oai.prompt
    assert "НЕ повторяй" in oai.prompt
