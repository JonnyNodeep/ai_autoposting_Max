from fastapi import APIRouter, Depends, HTTPException

from app.infrastructure.database.session import get_session
from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
from app.presentation.api.dependencies import require_api_token

schedule_router = APIRouter(
    prefix="/api",
    tags=["Schedule"],
    dependencies=[Depends(require_api_token)],
)


@schedule_router.get("/channels/{channel_id}/schedule")
async def get_channel_schedule(channel_id: int) -> list[dict]:
    async for session in get_session():
        repo = SQLAPublishScheduleRepository(session)
        schedules = await repo.get_by_channel(channel_id)
        return [
            {
                "id": s.id,
                "post_id": s.post_id,
                "channel_id": s.channel_id,
                "scheduled_at": s.scheduled_at.isoformat(),
                "sent_to_owner_at": s.sent_to_owner_at.isoformat() if s.sent_to_owner_at else None,
                "confirmed_at": s.confirmed_at.isoformat() if s.confirmed_at else None,
                "published_at": s.published_at.isoformat() if s.published_at else None,
                "status": s.status.value,
            }
            for s in schedules
        ]


@schedule_router.delete("/schedule/{schedule_id}")
async def cancel_schedule(schedule_id: int) -> dict:
    async for session in get_session():
        repo = SQLAPublishScheduleRepository(session)
        await repo.delete(schedule_id)
        await session.commit()
        return {"status": "cancelled"}
