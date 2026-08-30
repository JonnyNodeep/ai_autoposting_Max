from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.channel import Channel
from app.domain.interfaces.channel_repository import ChannelRepository
from app.domain.value_objects.style_profile import StyleProfile
from app.infrastructure.models.channel import ChannelModel


class SQLAlchemyChannelRepository(ChannelRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, channel_id: int) -> Channel | None:
        stmt = select(ChannelModel).where(ChannelModel.id == channel_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_max_chat_id(self, max_chat_id: int) -> Channel | None:
        stmt = select(ChannelModel).where(ChannelModel.max_chat_id == max_chat_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_owner(self, owner_id: int) -> list[Channel]:
        stmt = select(ChannelModel).where(
            ChannelModel.owner_id == owner_id,
            ChannelModel.is_active == True,
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def get_all(self) -> list[Channel]:
        stmt = select(ChannelModel).where(ChannelModel.is_active == True)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def create(self, channel: Channel) -> Channel:
        model = ChannelModel(
            owner_id=channel.owner_id,
            max_chat_id=channel.max_chat_id,
            title=channel.title,
            description=channel.description,
            topic=channel.topic,
            style=channel.style,
            sample_posts=channel.sample_posts,
            logo_token=channel.logo_token,
            logo_path=channel.logo_path,
            content_frequency=channel.content_frequency,
            style_profile=channel.style_profile.to_dict(),
            is_setup_complete=channel.is_setup_complete,
            channel_link=channel.channel_link,
            telegram_chat_id=channel.telegram_chat_id,
            telegram_link=channel.telegram_link,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update(self, channel: Channel) -> Channel:
        await self._session.execute(
            update(ChannelModel)
            .where(ChannelModel.id == channel.id)
            .values(
                owner_id=channel.owner_id,
                title=channel.title,
                description=channel.description,
                topic=channel.topic,
                style=channel.style,
                style_profile=channel.style_profile.to_dict(),
                sample_posts=channel.sample_posts,
                logo_token=channel.logo_token,
                logo_path=channel.logo_path,
                content_frequency=channel.content_frequency,
                is_active=channel.is_active,
                is_setup_complete=channel.is_setup_complete,
                channel_link=channel.channel_link,
                telegram_chat_id=channel.telegram_chat_id,
                telegram_link=channel.telegram_link,
            )
        )
        await self._session.flush()
        return channel

    async def delete(self, channel_id: int) -> None:
        await self._session.execute(
            update(ChannelModel).where(ChannelModel.id == channel_id).values(is_active=False)
        )
        await self._session.flush()

    async def count_by_owner(self, owner_id: int) -> int:
        stmt = select(func.count()).where(
            ChannelModel.owner_id == owner_id,
            ChannelModel.is_active == True,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    @staticmethod
    def _to_entity(model: ChannelModel) -> Channel:
        return Channel(
            id=model.id,
            owner_id=model.owner_id,
            max_chat_id=model.max_chat_id,
            title=model.title,
            description=model.description,
            topic=model.topic,
            style=model.style,
            sample_posts=model.sample_posts or [],
            logo_token=model.logo_token,
            logo_path=model.logo_path,
            content_frequency=model.content_frequency,
            style_profile=StyleProfile.from_dict(model.style_profile) if model.style_profile else StyleProfile(),
            created_at=model.created_at,
            is_active=model.is_active,
            is_setup_complete=model.is_setup_complete,
            channel_link=model.channel_link,
            telegram_chat_id=model.telegram_chat_id,
            telegram_link=model.telegram_link,
        )
