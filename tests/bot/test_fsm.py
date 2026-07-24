import pytest
from app.bot.states.channel_setup import ChannelSetupFSM, SetupStep


class MockRedis:
    def __init__(self):
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


@pytest.mark.asyncio
async def test_fsm_start():
    redis = MockRedis()
    fsm = ChannelSetupFSM(redis)
    state = await fsm.start(1, 42)
    assert state["step"] == SetupStep.TOPIC
    assert state["channel_id"] == 42


@pytest.mark.asyncio
async def test_fsm_advance():
    redis = MockRedis()
    fsm = ChannelSetupFSM(redis)
    await fsm.start(1, 42)
    state = await fsm.advance(1, SetupStep.FREQUENCY)
    assert state["step"] == SetupStep.FREQUENCY


@pytest.mark.asyncio
async def test_fsm_set_data():
    redis = MockRedis()
    fsm = ChannelSetupFSM(redis)
    await fsm.start(1, 42)
    state = await fsm.set_data(1, {"topic": "tech"})
    assert state["topic"] == "tech"
    assert state["step"] == SetupStep.TOPIC


@pytest.mark.asyncio
async def test_fsm_clear_state():
    redis = MockRedis()
    fsm = ChannelSetupFSM(redis)
    await fsm.start(1, 42)
    await fsm.clear_state(1)
    state = await fsm.get_state(1)
    assert state is None
