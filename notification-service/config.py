from functools import lru_cache
from socket import gethostname

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str = Field(validation_alias="BOT_TOKEN")
    webhook_secret: str = Field(validation_alias="WEBHOOK_SECRET")
    supabase_url: str = Field(default="http://supabase-kong:8000", validation_alias="SUPABASE_URL")
    supabase_key: str = Field(validation_alias=AliasChoices("SUPABASE_KEY", "SUPABASE_SERVICE_KEY"))
    redis_url: str = Field(default="redis://redis:6379", validation_alias="REDIS_URL")
    telegram_api_url: str = Field(default="http://telegram-bot-api:8081", validation_alias="TELEGRAM_API_URL")

    discourse_links_table: str = Field(validation_alias="DISCOURSE_LINKS_TABLE")
    discourse_base_url: str = Field(validation_alias="DISCOURSE_BASE_URL")

    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    port: int = Field(default=8067, validation_alias="PORT")
    log_level: str = Field(default="DEBUG", validation_alias="LOG_LEVEL")
    log_notification_data: bool = Field(default=False, validation_alias="NOTIFICATION_LOG_PAYLOAD_DATA")

    redis_stream: str = Field(default="tg_notifications", validation_alias="REDIS_STREAM")
    redis_group: str = Field(default="drain", validation_alias="REDIS_GROUP")
    redis_consumer: str | None = Field(default=None, validation_alias="REDIS_CONSUMER")
    stream_maxlen: int = Field(default=10000, validation_alias="STREAM_MAXLEN")
    idempotency_ttl_seconds: int = Field(default=86400, validation_alias="IDEMPOTENCY_TTL_SECONDS")

    drain_batch_size: int = Field(default=25, validation_alias="DRAIN_BATCH_SIZE")
    drain_block_ms: int = Field(default=5000, validation_alias="DRAIN_BLOCK_MS")
    max_attempts: int = Field(default=5, validation_alias="MAX_ATTEMPTS")

    reaper_interval_seconds: int = Field(default=120, validation_alias="REAPER_INTERVAL_SECONDS")
    reaper_idle_ms: int = Field(default=120000, validation_alias="REAPER_IDLE_MS")
    reaper_batch_size: int = Field(default=100, validation_alias="REAPER_BATCH_SIZE")

    telegram_timeout_seconds: float = Field(default=10.0, validation_alias="TELEGRAM_TIMEOUT_SECONDS")
    supabase_timeout_seconds: float = Field(default=3.0, validation_alias="SUPABASE_TIMEOUT_SECONDS")
    telegram_global_rate_per_second: float = Field(default=25.0, validation_alias="TELEGRAM_GLOBAL_RATE_PER_SECOND")
    telegram_chat_min_interval_seconds: float = Field(
        default=1.05,
        validation_alias="TELEGRAM_CHAT_MIN_INTERVAL_SECONDS",
    )

    @field_validator("supabase_url", "telegram_api_url", "discourse_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def consumer_name(self) -> str:
        return self.redis_consumer or f"notification-service-{gethostname()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
