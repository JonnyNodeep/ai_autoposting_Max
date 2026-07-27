from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class PipelineStatus(StrEnum):
    ACTIVE = "active"
    STOPPED = "stopped"


@dataclass(kw_only=True)
class PipelineRun:
    id: int | None = None
    user_id: int
    max_user_id: int = 0
    channel_id: int
    channel_link: str = ""
    blocks_config: dict[str, Any] | None = None
    frequency: str = "daily"
    times: list[str] | None = None
    status: PipelineStatus = PipelineStatus.ACTIVE
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime | None = None
