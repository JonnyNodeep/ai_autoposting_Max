from datetime import datetime, UTC

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.rss_seen_item import RssSeenItem
from app.infrastructure.models.rss_seen_item import RssSeenItemModel


class SQLARssSeenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_any_for_channel(self, channel_id: int) -> bool:
        stmt = (
            select(RssSeenItemModel.id)
            .where(RssSeenItemModel.channel_id == channel_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def is_seen(self, channel_id: int, *, guid: str, url: str = "") -> bool:
        conditions = [RssSeenItemModel.item_guid == guid]
        if url:
            conditions.append(RssSeenItemModel.item_url == url)
        stmt = (
            select(RssSeenItemModel.id)
            .where(
                RssSeenItemModel.channel_id == channel_id,
                or_(*conditions),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_seen_guids_and_urls(self, channel_id: int) -> tuple[set[str], set[str]]:
        stmt = select(
            RssSeenItemModel.item_guid,
            RssSeenItemModel.item_url,
        ).where(RssSeenItemModel.channel_id == channel_id)
        result = await self._session.execute(stmt)
        guids: set[str] = set()
        urls: set[str] = set()
        for guid, url in result.all():
            if guid:
                guids.add(guid)
            if url:
                urls.add(url)
        return guids, urls

    async def count_published_since(self, channel_id: int, since: datetime) -> int:
        stmt = select(func.count()).select_from(RssSeenItemModel).where(
            RssSeenItemModel.channel_id == channel_id,
            RssSeenItemModel.processed_at >= since,
            RssSeenItemModel.pipeline_run_id.is_not(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def mark_seen(self, item: RssSeenItem) -> bool:
        if await self.is_seen(item.channel_id, guid=item.item_guid, url=item.item_url or ""):
            return False
        self._session.add(
            RssSeenItemModel(
                channel_id=item.channel_id,
                pipeline_run_id=item.pipeline_run_id,
                feed_url=item.feed_url[:1024],
                item_guid=item.item_guid[:1024],
                item_url=(item.item_url or "")[:1024],
                title=(item.title or "")[:1024],
                published_at=item.published_at,
                processed_at=item.processed_at or datetime.now(UTC),
            )
        )
        await self._session.flush()
        return True

    async def mark_many(self, items: list[RssSeenItem]) -> int:
        inserted = 0
        for it in items:
            if await self.mark_seen(it):
                inserted += 1
        return inserted
