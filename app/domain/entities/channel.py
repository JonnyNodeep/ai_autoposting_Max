from dataclasses import dataclass, field
from datetime import datetime, UTC

from app.domain.value_objects.style_profile import StyleProfile


@dataclass(kw_only=True)
class Channel:
    id: int | None = None
    owner_id: int
    max_chat_id: int
    title: str
    description: str | None = None
    topic: str | None = None
    style: str | None = None
    style_profile: StyleProfile = field(default_factory=StyleProfile)
    sample_posts: list[str] = field(default_factory=list)
    logo_token: str | None = None
    logo_path: str | None = None
    content_frequency: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_active: bool = True
    is_setup_complete: bool = False
    channel_link: str | None = None
    telegram_chat_id: int | None = None
    telegram_link: str | None = None
