from pydantic import BaseModel, ConfigDict


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tier: str
    status: str
    channels_limit: int
    posts_per_day: int = 1
    generations_quota: int = 30
    generations_used: int = 0
    expires_at: str
