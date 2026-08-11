from dataclasses import dataclass, field
from datetime import datetime, UTC


@dataclass(kw_only=True)
class User:
    id: int | None = None
    max_user_id: int
    username: str | None = None
    first_name: str
    last_name: str | None = None
    discount_percent: int = 0
    referral_code: str | None = None
    referred_by_user_id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_active: bool = True

    @property
    def full_name(self) -> str:
        parts = [self.first_name]
        if self.last_name:
            parts.append(self.last_name)
        return " ".join(parts)
