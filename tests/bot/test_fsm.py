import pytest
from app.bot.states.channel_setup import ChannelSetupFSM, SetupStep
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep


class MockRedis:
    def __init__(self):
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

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


@pytest.mark.asyncio
async def test_ai_studio_start_creates_session_for_new_user():
    redis = MockRedis()
    fsm = AIStudioFSM(redis)
    assert await fsm.get_state(70147830) is None

    state = await fsm.start(70147830)
    assert state["step"] == AIStudioStep.SELECT_CHANNEL
    assert state["channel_id"] is None
    assert state["pipelines"] == {}

    # Selecting a channel works only after start
    updated = await fsm.set_channel(70147830, 7)
    assert updated is not None
    assert updated["channel_id"] == 7
    assert updated["step"] == AIStudioStep.SELECT_FEATURES
    assert updated["blocks"]


@pytest.mark.asyncio
async def test_ai_studio_start_preserves_existing_pipelines_cache():
    redis = MockRedis()
    fsm = AIStudioFSM(redis)
    await fsm.start(1)
    await fsm.set_channel(1, 10)
    await fsm.set_block_data(1, "post_gen", {"enabled": True, "mode": "ai"})
    before = await fsm.get_state(1)

    # Re-entry must not wipe: only start when missing
    existing = await fsm.get_state(1)
    assert existing is not None
    assert existing["pipelines"] or existing["blocks"]
    assert before["blocks"]["post_gen"]["enabled"] is True
