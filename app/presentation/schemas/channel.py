from pydantic import BaseModel, ConfigDict, Field


class ChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    max_chat_id: int
    title: str
    description: str | None = None
    topic: str | None = None
    style: str | None = None
    style_profile: dict | None = None
    content_frequency: str | None = None
    is_active: bool = True
    is_setup_complete: bool = False
    telegram_chat_id: int | None = None
    telegram_link: str | None = None


class ChannelCreateRequest(BaseModel):
    max_chat_id: int = Field(gt=0, description="MAX chat ID (must be positive)")


class ChannelUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    topic: str | None = Field(default=None, max_length=512)
    content_frequency: str | None = Field(default=None, max_length=64)
    telegram_chat_id: int | None = Field(
        default=None,
        description="Telegram channel chat_id for dual-publish (None = Max only)",
    )
    telegram_link: str | None = Field(
        default=None,
        max_length=512,
        description="Public Telegram link https://t.me/... for CTA in TG posts",
    )


class SamplePostsResponse(BaseModel):
    channel_id: int
    count: int
    posts: list[str]


class StyleProfileResponse(BaseModel):
    channel_id: int
    tone: str
    audience: str
    topics: list[str]
    format_preference: str
    avg_length: int
    features: list[str]


class DescriptionResponse(BaseModel):
    channel_id: int
    description: str


class LogoResponse(BaseModel):
    channel_id: int
    logo_url: str
