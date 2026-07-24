from pydantic import BaseModel, ConfigDict


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tier: str
    status: str
    channels_limit: int
    expires_at: str
