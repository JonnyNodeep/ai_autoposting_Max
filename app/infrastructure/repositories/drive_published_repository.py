from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models.drive_published_item import DrivePublishedItemModel


class SQLADrivePublishedRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_published_file_ids(self, channel_id: int) -> set[str]:
        stmt = select(DrivePublishedItemModel.drive_file_id).where(
            DrivePublishedItemModel.channel_id == channel_id
        )
        result = await self._session.execute(stmt)
        return {str(row[0]) for row in result.all() if row[0]}

    async def is_published(self, channel_id: int, drive_file_id: str) -> bool:
        stmt = (
            select(DrivePublishedItemModel.id)
            .where(
                DrivePublishedItemModel.channel_id == channel_id,
                DrivePublishedItemModel.drive_file_id == drive_file_id,
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def mark_published(
        self,
        *,
        channel_id: int,
        drive_file_id: str,
        file_name: str = "",
        pipeline_run_id: int | None = None,
    ) -> bool:
        if await self.is_published(channel_id, drive_file_id):
            return False
        self._session.add(
            DrivePublishedItemModel(
                channel_id=channel_id,
                pipeline_run_id=pipeline_run_id,
                drive_file_id=drive_file_id[:128],
                file_name=(file_name or "")[:1024],
                published_at=datetime.now(UTC),
            )
        )
        await self._session.flush()
        return True

    async def count_unpublished(
        self, channel_id: int, all_file_ids: list[str]
    ) -> int:
        published = await self.get_published_file_ids(channel_id)
        return sum(1 for fid in all_file_ids if fid not in published)
