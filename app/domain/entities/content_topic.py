from dataclasses import dataclass, field
from enum import StrEnum


class TopicStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    SKIPPED = "skipped"


@dataclass(kw_only=True)
class ContentTopic:
    id: int | None = None
    plan_id: int
    topic: str
    scheduled_date: str | None = None
    order: int = 0
    is_ai_generated: bool = True
    status: TopicStatus = TopicStatus.PENDING
