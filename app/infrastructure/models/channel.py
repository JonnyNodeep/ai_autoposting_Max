from datetime import datetime
from sqlalchemy import Integer, BigInteger, String, Boolean, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class ChannelModel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    max_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(256), nullable=True)
    style: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    style_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sample_posts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    logo_token: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content_frequency: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_setup_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    channel_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
