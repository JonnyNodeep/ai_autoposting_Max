from abc import ABC, abstractmethod

from app.domain.entities.publish_schedule import PublishSchedule


class PublishScheduleRepository(ABC):
    @abstractmethod
    async def get_by_id(self, schedule_id: int) -> PublishSchedule | None: ...

    @abstractmethod
    async def get_by_channel(self, channel_id: int, limit: int = 50) -> list[PublishSchedule]: ...

    @abstractmethod
    async def get_by_plan(self, plan_id: int) -> list[PublishSchedule]: ...

    @abstractmethod
    async def get_due_posts(self, before: object) -> list[PublishSchedule]: ...

    @abstractmethod
    async def get_expired_confirmations(self, older_than_hours: int = 6) -> list[PublishSchedule]: ...

    @abstractmethod
    async def create(self, schedule: PublishSchedule) -> PublishSchedule: ...

    @abstractmethod
    async def update(self, schedule: PublishSchedule) -> PublishSchedule: ...

    @abstractmethod
    async def delete(self, schedule_id: int) -> None: ...
