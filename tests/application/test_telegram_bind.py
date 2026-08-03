import pytest

from app.application.channels.telegram_bind import bind_telegram_chat, unbind_telegram
from app.domain.entities.channel import Channel


class _Repo:
    def __init__(self):
        self.saved = None

    async def update(self, channel: Channel) -> Channel:
        self.saved = channel
        return channel


@pytest.mark.asyncio
async def test_bind_telegram_chat_sets_link_from_get_chat():
    class _Tg:
        configured = True

        async def get_chat(self, chat_id):
            return {"title": "Bio", "username": "bio_demo"}

        async def close(self):
            return None

    channel = Channel(owner_id=1, max_chat_id=10, title="Max Bio")
    repo = _Repo()
    result = await bind_telegram_chat(
        channel,
        -1001,
        channel_repo=repo,
        tg_client=_Tg(),
    )
    assert result.ok
    assert result.need_manual_link is False
    assert channel.telegram_chat_id == -1001
    assert channel.telegram_link == "https://t.me/bio_demo"


@pytest.mark.asyncio
async def test_bind_telegram_chat_private_needs_manual_link():
    class _Tg:
        configured = True

        async def get_chat(self, chat_id):
            return {"title": "Private"}

        async def close(self):
            return None

    channel = Channel(owner_id=1, max_chat_id=10, title="Max Bio")
    result = await bind_telegram_chat(
        channel,
        -1002,
        channel_repo=_Repo(),
        tg_client=_Tg(),
    )
    assert result.ok
    assert result.need_manual_link is True
    assert channel.telegram_chat_id == -1002
    assert channel.telegram_link is None


@pytest.mark.asyncio
async def test_unbind_telegram_clears_fields():
    channel = Channel(
        owner_id=1,
        max_chat_id=10,
        title="X",
        telegram_chat_id=-1,
        telegram_link="https://t.me/x",
    )
    result = await unbind_telegram(channel, channel_repo=_Repo())
    assert result.ok
    assert channel.telegram_chat_id is None
    assert channel.telegram_link is None
