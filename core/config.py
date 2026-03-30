from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "RentEase Nigeria"
    APP_ENV: str = "development"
    DEBUG: bool = True
    BASE_URL: str = "http://localhost:8000"

    SECRET_KEY: str = "change-me-please-change-me-please"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    ALGORITHM: str = "HS256"

    DATABASE_URL: str = "postgresql://postgres:nBWuEIbsINIXLdNhdadwDgDtYSRjBXPg@hopper.proxy.rlwy.net:24280/railway"
    REDIS_URL: str = "redis://default:OAhuJJXiSzTHQGRoQvmitfNpnKafVXEN@redis.railway.internal:6379"
    REDIS_STATE_TTL_SECONDS: int = 3600
    REDIS_RESUME_TTL_SECONDS: int = 2592000

    WHATSAPP_PHONE_NUMBER_ID: str = "your_phone_number_id"
    WHATSAPP_ACCESS_TOKEN: str = "your_whatsapp_access_token"
    WHATSAPP_VERIFY_TOKEN: str = "rentease_webhook_verify_2024"
    WHATSAPP_API_VERSION: str = "v19.0"

    PAYSTACK_SECRET_KEY: str = "your_paystack_secret_key"
    PAYSTACK_PUBLIC_KEY: str = "your_paystack_public_key"
    PAYSTACK_WEBHOOK_SECRET: str = ""

    CLOUDINARY_CLOUD_NAME: str = "your_cloud_name"
    CLOUDINARY_API_KEY: str = "your_api_key"
    CLOUDINARY_API_SECRET: str = "your_api_secret"

    ADMIN_EMAIL: str = "admin@rentease.ng"
    ADMIN_PASSWORD: str = "ChangeThisStrongPassword123!"

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
