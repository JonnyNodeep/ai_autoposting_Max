from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class RssSeenItemModel(Base):
    __tablename__ = "rss_seen_items"
    __table_args__ = (
        UniqueConstraint("channel_id", "item_guid", name="uq_rss_seen_channel_guid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id"), nullable=False, index=True
    )
    pipeline_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_runs.id"), nullable=True
    )
    feed_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    item_guid: Mapped[str] = mapped_column(String(1024), nullable=False)
    item_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
