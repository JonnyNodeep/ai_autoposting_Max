from datetime import datetime
from sqlalchemy import Integer, Boolean, String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class PublishScheduleModel(Base):
    __tablename__ = "publish_schedules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("content_plans.id"), nullable=True, index=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("content_topics.id"), nullable=True, index=True)
    post_id: Mapped[int | None] = mapped_column(ForeignKey("content_posts.id"), nullable=True, index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    sent_to_owner_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
