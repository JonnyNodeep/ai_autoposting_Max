from dataclasses import dataclass, field
from datetime import datetime, UTC


@dataclass(kw_only=True)
class GenerationLog:
    id: int | None = None
    user_id: int
    channel_id: int | None = None
    operation: str = ""
    tokens_used: int = 0
    model: str = ""
    estimated_cost: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
