from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    max_user_id: int
    username: str | None
    first_name: str
    last_name: str | None
    is_active: bool


class UserRegisterRequest(BaseModel):
    max_user_id: int
    username: str | None = None
    first_name: str = ""
    last_name: str | None = None
