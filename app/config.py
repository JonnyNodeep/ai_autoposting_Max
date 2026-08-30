from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


class AppSettings(BaseSettings):
    model_config = {"env_prefix": "APP_", "extra": "ignore"}

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    debug: bool = Field(default=False)
    webhook_url: str = Field(default="")
    webhook_secret: str = Field(default="")
    api_token: str = Field(default="")
    consume_quota_only_on_publish: bool = Field(default=True)


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
    admin_api_key: str = Field(default="", alias="OPENAI_ADMIN_API_KEY")
    text_model: str = Field(default="gpt-5.5-mini", alias="OPENAI_TEXT_MODEL")
    image_model: str = Field(default="imagen-1.5", alias="OPENAI_IMAGE_MODEL")
    image_quality: str = Field(default="medium", alias="OPENAI_IMAGE_QUALITY")
    search_model: str = Field(default="gpt-4o-mini-search-preview", alias="OPENAI_SEARCH_MODEL")
    tts_model: str = Field(default="gpt-4o-mini-tts", alias="OPENAI_TTS_MODEL")
    tale_model: str = Field(default="gpt-5.4", alias="OPENAI_TALE_MODEL")
    tale_image_size: str = Field(default="1536x1024", alias="TALE_IMAGE_SIZE")
    tale_image_quality: str = Field(default="low", alias="TALE_IMAGE_QUALITY")

    @field_validator("image_quality")
    @classmethod
    def validate_image_quality(cls, value: str) -> str:
        allowed = {"low", "medium", "high"}
        normalized = (value or "medium").strip().lower()
        if normalized not in allowed:
            raise ValueError(f"OPENAI_IMAGE_QUALITY must be one of: {sorted(allowed)}")
        return normalized

    @field_validator("tale_image_quality")
    @classmethod
    def validate_tale_image_quality(cls, value: str) -> str:
        allowed = {"low", "medium", "high"}
        normalized = (value or "low").strip().lower()
        if normalized not in allowed:
            raise ValueError(f"TALE_IMAGE_QUALITY must be one of: {sorted(allowed)}")
        return normalized


class YooKassaSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    secret_key: str = Field(default="", alias="YOOKASSA_SECRET_KEY")


class AdminSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    max_user_id: int = Field(default=0, alias="ADMIN_MAX_USER_ID")
    api_token: str = Field(default="", alias="ADMIN_API_TOKEN")
    web_password: str = Field(default="", alias="ADMIN_WEB_PASSWORD")
    session_secret: str = Field(default="", alias="ADMIN_SESSION_SECRET")


class VidGoSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    api_key: str = Field(default="", alias="VIDGO_API_KEY")
    callback_url: str = Field(default="", alias="VIDGO_CALLBACK_URL")
    webhook_token: str = Field(default="", alias="VIDGO_WEBHOOK_TOKEN")


class TelegramSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    token: str = Field(default="", alias="TELEGRAM_TOKEN")


class YandexSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    folder_id: str = Field(default="", alias="YANDEX_FOLDER_ID")
    speechkit_api_key: str = Field(default="", alias="YANDEX_SPEECHKIT_API_KEY")
    tts_proxy: str = Field(default="", alias="YANDEX_TTS_PROXY")


class SunorSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    api_key: str = Field(default="", alias="SUNOR_API_KEY")
    base_url: str = Field(default="https://sunor.cc/api/v1", alias="SUNOR_BASE_URL")
    poll_timeout_s: int = Field(default=900, alias="SUNOR_POLL_TIMEOUT_S")


class RssSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    http_proxy: str = Field(default="", alias="RSS_HTTP_PROXY")


class GoogleDriveSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    service_account_json: str = Field(default="", alias="GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON")
    service_account_json_b64: str = Field(
        default="", alias="GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_B64"
    )


class FeatureSettings(BaseSettings):
    model_config = {"extra": "ignore"}

    rss_whitelist: str = Field(default="", alias="FEATURE_RSS_WHITELIST")
    video_whitelist: str = Field(default="", alias="FEATURE_VIDEO_WHITELIST")
    audio_whitelist: str = Field(default="", alias="FEATURE_AUDIO_WHITELIST")
    drive_whitelist: str = Field(default="", alias="FEATURE_DRIVE_WHITELIST")
    high_freq_whitelist: str = Field(default="", alias="FEATURE_HIGH_FREQ_WHITELIST")


class Settings(BaseSettings):
    app: AppSettings = AppSettings()
    postgres: PostgresSettings = PostgresSettings()
    redis: RedisSettings = RedisSettings()
    max_api: MaxAPISettings = MaxAPISettings()
    openai: OpenAISettings = OpenAISettings()
    yookassa: YooKassaSettings = YooKassaSettings()
    admin: AdminSettings = AdminSettings()
    vidgo: VidGoSettings = VidGoSettings()
    telegram: TelegramSettings = TelegramSettings()
    yandex: YandexSettings = YandexSettings()
    sunor: SunorSettings = SunorSettings()
    rss: RssSettings = RssSettings()
    google_drive: GoogleDriveSettings = GoogleDriveSettings()
    features: FeatureSettings = FeatureSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
