from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum


class ScheduleStatus(StrEnum):
    SCHEDULED = "scheduled"
    SENT_TO_OWNER = "sent_to_owner"
    CONFIRMED = "confirmed"
    PUBLISHED = "published"
    SKIPPED = "skipped"
    EXPIRED = "expired"


@dataclass(kw_only=True)
class PublishSchedule:
    id: int | None = None
    plan_id: int | None = None
    topic_id: int | None = None
    post_id: int | None = None
    channel_id: int
    scheduled_at: datetime
    sent_to_owner_at: datetime | None = None
    confirmed_at: datetime | None = None
    published_at: datetime | None = None
    auto_publish: bool = False
    status: ScheduleStatus = ScheduleStatus.SCHEDULED
