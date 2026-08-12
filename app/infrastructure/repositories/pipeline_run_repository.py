from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.pipeline_run import PipelineRun, PipelineStatus
from app.infrastructure.models.pipeline_run import PipelineRunModel


class SQLAPipelineRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, run_id: int) -> PipelineRun | None:
        stmt = select(PipelineRunModel).where(PipelineRunModel.id == run_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_active_by_channel(self, channel_id: int) -> PipelineRun | None:
        """Latest active run for channel (safe if duplicates exist)."""
        stmt = (
            select(PipelineRunModel)
            .where(
                PipelineRunModel.channel_id == channel_id,
                PipelineRunModel.status == PipelineStatus.ACTIVE.value,
            )
            .order_by(PipelineRunModel.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_latest_by_channel(self, channel_id: int) -> PipelineRun | None:
        """Latest run for channel regardless of status (active or stopped)."""
        stmt = (
            select(PipelineRunModel)
            .where(PipelineRunModel.channel_id == channel_id)
            .order_by(PipelineRunModel.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_active_by_channel(self, channel_id: int) -> list[PipelineRun]:
        stmt = (
            select(PipelineRunModel)
            .where(
                PipelineRunModel.channel_id == channel_id,
                PipelineRunModel.status == PipelineStatus.ACTIVE.value,
            )
            .order_by(PipelineRunModel.id.desc())
        )
        result = await self._session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def stop_all_active_by_channel(self, channel_id: int) -> list[int]:
        """Stop every active run for channel. Returns stopped run ids."""
        runs = await self.list_active_by_channel(channel_id)
        ids = [r.id for r in runs if r.id is not None]
        if not ids:
            return []
        await self._session.execute(
            update(PipelineRunModel)
            .where(PipelineRunModel.id.in_(ids))
            .values(status=PipelineStatus.STOPPED.value)
        )
        await self._session.flush()
        return ids

    async def get_all_active(self) -> list[PipelineRun]:
        stmt = select(PipelineRunModel).where(
            PipelineRunModel.status == PipelineStatus.ACTIVE.value,
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def list_active_by_user(self, user_id: int) -> list[PipelineRun]:
        stmt = (
            select(PipelineRunModel)
            .where(
                PipelineRunModel.user_id == user_id,
                PipelineRunModel.status == PipelineStatus.ACTIVE.value,
            )
            .order_by(PipelineRunModel.id.desc())
        )
        result = await self._session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def stop_all_active_by_user(self, user_id: int) -> list[int]:
        runs = await self.list_active_by_user(user_id)
        ids = [r.id for r in runs if r.id is not None]
        if not ids:
            return []
        await self._session.execute(
            update(PipelineRunModel)
            .where(PipelineRunModel.id.in_(ids))
            .values(status=PipelineStatus.STOPPED.value)
        )
        await self._session.flush()
        return ids

    async def create(self, run: PipelineRun) -> PipelineRun:
        model = PipelineRunModel(
            user_id=run.user_id,
            max_user_id=run.max_user_id,
            channel_id=run.channel_id,
            channel_link=run.channel_link,
            blocks_config=run.blocks_config,
            frequency=run.frequency,
            times=run.times,
            status=run.status.value,
            next_run_at=run.next_run_at,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update(self, run: PipelineRun) -> PipelineRun:
        await self._session.execute(
            update(PipelineRunModel)
            .where(PipelineRunModel.id == run.id)
            .values(
                status=run.status.value,
                last_run_at=run.last_run_at,
                next_run_at=run.next_run_at,
                blocks_config=run.blocks_config,
                frequency=run.frequency,
                times=run.times,
                channel_link=run.channel_link,
            )
        )
        await self._session.flush()
        return run

    async def delete(self, run_id: int) -> None:
        await self._session.execute(
            delete(PipelineRunModel).where(PipelineRunModel.id == run_id)
        )
        await self._session.flush()

    @staticmethod
    def _to_entity(model: PipelineRunModel) -> PipelineRun:
        return PipelineRun(
            id=model.id,
            user_id=model.user_id,
            max_user_id=model.max_user_id,
            channel_id=model.channel_id,
            channel_link=model.channel_link,
            blocks_config=model.blocks_config,
            frequency=model.frequency,
            times=model.times,
            status=PipelineStatus(model.status),
            last_run_at=model.last_run_at,
            next_run_at=model.next_run_at,
            created_at=model.created_at,
        )
