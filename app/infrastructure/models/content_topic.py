from sqlalchemy import Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class ContentTopicModel(Base):
    __tablename__ = "content_topics"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("content_plans.id"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(1024), nullable=False)
    scheduled_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
