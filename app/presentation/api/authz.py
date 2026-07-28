from fastapi import HTTPException

from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository


async def ensure_channel_owner(session, channel_id: int, owner_id: int):
    channel_repo = SQLAlchemyChannelRepository(session)
    channel = await channel_repo.get_by_id(channel_id)
    if not channel or channel.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel
