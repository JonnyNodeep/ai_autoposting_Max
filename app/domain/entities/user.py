from dataclasses import dataclass, field
from datetime import datetime, UTC
from uuid import uuid4


@dataclass(kw_only=True)
class User:
    id: int | None = None
    max_user_id: int
    username: str | None = None
    first_name: str
    last_name: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_active: bool = True

    @property
    def full_name(self) -> str:
        parts = [self.first_name]
        if self.last_name:
            parts.append(self.last_name)
        return " ".join(parts)
