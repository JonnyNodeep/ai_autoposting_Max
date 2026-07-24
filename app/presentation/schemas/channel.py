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


class ChannelCreateRequest(BaseModel):
    max_chat_id: int = Field(gt=0, description="MAX chat ID (must be positive)")


class ChannelUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    topic: str | None = Field(default=None, max_length=512)
    content_frequency: str | None = Field(default=None, max_length=64)


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


class PlanResponse(BaseModel):
    id: int
    channel_id: int
    duration_days: int
    status: str
    created_at: str


class PlanCreateRequest(BaseModel):
    duration_days: int = Field(ge=1, le=365, description="Plan duration in days (1-365)")


class TopicResponse(BaseModel):
    id: int
    plan_id: int
    topic: str
    scheduled_date: str
    order: int
    is_ai_generated: bool
    status: str


class TopicUpdateRequest(BaseModel):
    topic: str | None = Field(default=None, max_length=512)


class PostResponse(BaseModel):
    id: int
    topic_id: int
    title: str
    text: str
    cta: str
    image_prompt: str
    image_url: str | None = None
    status: str


class PublishRequest(BaseModel):
    chat_id: int
