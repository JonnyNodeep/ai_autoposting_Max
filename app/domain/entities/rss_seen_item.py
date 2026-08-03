from dataclasses import dataclass, field
from datetime import datetime, UTC


@dataclass(kw_only=True)
class RssSeenItem:
    id: int | None = None
    channel_id: int
    pipeline_run_id: int | None = None
    feed_url: str
    item_guid: str
    item_url: str = ""
    title: str = ""
    published_at: datetime | None = None
    processed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
