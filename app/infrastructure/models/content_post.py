from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class ContentPostModel(Base):
    __tablename__ = "content_posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("content_topics.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    text: Mapped[str] = mapped_column(String(4096), nullable=False, default="")
    cta: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    image_prompt: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
