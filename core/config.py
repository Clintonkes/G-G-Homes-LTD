"""Central settings module that loads environment variables and shared application configuration."""

from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "G & G Homes Ltd"
    APP_ENV: str = "development"
    DEBUG: bool = True
    BASE_URL: str = "http://localhost:8000"

    SECRET_KEY: str = "change-me-please-change-me-please"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    ALGORITHM: str = "HS256"

    DATABASE_URL: str = "postgresql+asyncpg://postgres:JdcpRAFSSXPhiNONmtdobiIqTurRQhrZ@hopper.proxy.rlwy.net:57917/railway"
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_STATE_TTL_SECONDS: int = 3600
    REDIS_RESUME_TTL_SECONDS: int = 2592000

    WHATSAPP_PHONE_NUMBER_ID: str = "1038728285994021"
    WHATSAPP_ACCESS_TOKEN: str = "EAAM8cE1YjP0BRF6ivfFt8SjHVZAPadjR8WEWDH0tK0cEZBKk3rFUZBoz9W8xrcPwuZCOlX5ZAePD59KUhmZA4J84VInS4U7AcIYMBZAd7QU3Sm5uZAtEzu22wjkHp4fuPLyKQaPZBZBVodAsZAquZArsmkriZBg04FtIKdmut1q4ywUTGwyuZAjCWZBUyoQCVW6OS4p1AZDZD"
    WHATSAPP_VERIFY_TOKEN: str = "Rentease"
    WHATSAPP_API_VERSION: str = "v19.0"

    PAYSTACK_SECRET_KEY: str = "your_paystack_secret_key"
    PAYSTACK_PUBLIC_KEY: str = "your_paystack_public_key"
    PAYSTACK_WEBHOOK_SECRET: str = ""

    CLOUDINARY_CLOUD_NAME: str | None = None
    CLOUDINARY_API_KEY: str | None = None
    CLOUDINARY_API_SECRET: str | None = None

    ADMIN_EMAIL: str = "admin@rentease.ng"
    ADMIN_PASSWORD: str = "ChangeThisStrongPassword123!"

    LLM_INTENT_ENABLED: bool = False
    LLM_INTENT_API_URL: str = "https://api.openai.com/v1/responses"
    LLM_INTENT_API_KEY: str = ""
    LLM_INTENT_MODEL: str = "gpt-4o-mini"
    LLM_INTENT_TIMEOUT_SECONDS: int = 15
    LLM_INTENT_MIN_CONFIDENCE: float = 0.6
    LLM_INTENT_TEMPERATURE: float = 0.0
    LLM_INTENT_MAX_TOKENS: int = 80
    LLM_INTENT_ALWAYS_USE: bool = True

    TRANSACTION_FEE_PERCENT: float = 4.0
    BASIC_SUBSCRIPTION_MONTHLY: int = 1500
    STANDARD_SUBSCRIPTION_MONTHLY: int = 3000
    ANNUAL_SUBSCRIPTION: int = 15000
    VERIFICATION_FEE: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @computed_field
    @property
    def whatsapp_api_url(self) -> str:
        return (
            f"https://graph.facebook.com/"
            f"{self.WHATSAPP_API_VERSION}/"
            f"{self.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
