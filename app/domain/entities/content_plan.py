from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum


class PlanStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(kw_only=True)
class ContentPlan:
    id: int | None = None
    channel_id: int
    duration_days: int
    status: PlanStatus = PlanStatus.DRAFT
    post_settings: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
