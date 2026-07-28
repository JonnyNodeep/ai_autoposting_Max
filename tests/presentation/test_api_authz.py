import pytest
from fastapi import HTTPException

from app.domain.entities.channel import Channel
from app.domain.entities.user import User
from app.presentation.api.authz import ensure_channel_owner


@pytest.mark.asyncio
async def test_ensure_channel_owner_ok(session, user_repo, channel_repo):
    user = await user_repo.create(User(max_user_id=1, first_name="Owner"))
    await session.commit()
    channel = await channel_repo.create(
        Channel(owner_id=user.id, max_chat_id=100, title="Ch")
    )
    await session.commit()

    found = await ensure_channel_owner(session, channel.id, user.id)
    assert found.id == channel.id


@pytest.mark.asyncio
async def test_ensure_channel_owner_wrong_owner(session, user_repo, channel_repo):
    owner = await user_repo.create(User(max_user_id=1, first_name="Owner"))
    other = await user_repo.create(User(max_user_id=2, first_name="Other"))
    await session.commit()
    channel = await channel_repo.create(
        Channel(owner_id=owner.id, max_chat_id=100, title="Ch")
    )
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await ensure_channel_owner(session, channel.id, other.id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_ensure_channel_owner_missing(session):
    with pytest.raises(HTTPException) as exc:
        await ensure_channel_owner(session, 99999, 1)
    assert exc.value.status_code == 404
