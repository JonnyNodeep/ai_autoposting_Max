from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class PaymentModel(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    yookassa_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, default="")
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount_before_discount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="solo")
    posts_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    confirmation_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
