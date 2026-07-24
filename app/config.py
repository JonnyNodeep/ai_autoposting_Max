from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic import Field


class AppSettings(BaseSettings):
    model_config = {"env_prefix": "APP_", "extra": "ignore"}

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    debug: bool = Field(default=False)
    webhook_url: str = Field(default="")
    webhook_secret: str = Field(default="")
    api_token: str = Field(default="")


class PostgresSettings(BaseSettings):
    model_config = {"env_prefix": "POSTGRES_", "extra": "ignore"}

    host: str = Field(default="postgres")
    port: int = Field(default=5432)
    user: str = Field(default="maxstudio")
    password: str = Field(default="maxstudio")
    db: str = Field(default="maxstudio")

    @property
    def url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"

    @property
    def sync_url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


class RedisSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")


class MaxAPISettings(BaseSettings):
    model_config = {"extra": "ignore"}

    token: str = Field(default="", alias="MAXBOT_TOKEN")
    base_url: str = Field(default="https://platform-api2.max.ru")


class OpenAISettings(BaseSettings):
    model_config = {"extra": "ignore"}

    api_key: str = Field(default="", alias="OPENAI_API_KEY")
    text_model: str = Field(default="gpt-5.5-mini", alias="OPENAI_TEXT_MODEL")
    image_model: str = Field(default="imagen-1.5", alias="OPENAI_IMAGE_MODEL")
    search_model: str = Field(default="gpt-4o-mini-search-preview", alias="OPENAI_SEARCH_MODEL")


class YooKassaSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    secret_key: str = Field(default="", alias="YOOKASSA_SECRET_KEY")


class AdminSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    max_user_id: int = Field(default=0, alias="ADMIN_MAX_USER_ID")
    api_token: str = Field(default="", alias="ADMIN_API_TOKEN")


class Settings(BaseSettings):
    app: AppSettings = AppSettings()
    postgres: PostgresSettings = PostgresSettings()
    redis: RedisSettings = RedisSettings()
    max_api: MaxAPISettings = MaxAPISettings()
    openai: OpenAISettings = OpenAISettings()
    yookassa: YooKassaSettings = YooKassaSettings()
    admin: AdminSettings = AdminSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
