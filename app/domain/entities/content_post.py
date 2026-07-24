from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum


class PostStatus(StrEnum):
    DRAFT = "draft"
    GENERATING = "generating"
    READY = "ready"
    PUBLISHED = "published"


@dataclass(kw_only=True)
class ContentPost:
    id: int | None = None
    topic_id: int
    title: str = ""
    text: str = ""
    cta: str = ""
    image_prompt: str = ""
    image_url: str | None = None
    status: PostStatus = PostStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
