from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class SubscriptionModel(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="solo")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="trial")
    channels_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    posts_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    generations_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    generations_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expiry_notified_3d: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expiry_notified_1d: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expiry_notified_0d: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
