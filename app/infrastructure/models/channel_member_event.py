from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class ChannelMemberEventModel(Base):
    __tablename__ = "channel_member_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id"), nullable=False, index=True
    )
    max_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    max_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
