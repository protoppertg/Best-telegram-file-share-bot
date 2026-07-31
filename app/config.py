"""Central configuration loaded from environment via Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    BOT_TOKEN: str
    BOT_USERNAME: str = ""

    WEBHOOK_URL: str = ""
    WEBHOOK_PATH: str = "/webhook"
    WEBHOOK_SECRET: str = ""

    DATABASE_URL: str

    REDIS_URL: str = ""

    ADMIN_IDS: str = ""

    CHANNEL_ID: str

    FREE_SEARCH_LIMIT: int = 5
    PREMIUM_SEARCH_LIMIT: int = 100
    FREE_UPLOAD_LIMIT: int = 100
    PREMIUM_UPLOAD_LIMIT: int = 200

    CACHE_TTL_SECONDS: int = 300
    SEARCH_RESULTS_PER_PAGE: int = 10
    PREMIUM_DURATION_DAYS: int = 30
    MAX_FILE_SIZE_MB: int = 50
    MODERATION_ENABLED: bool = False
    USE_POLLING: bool = False

    WEB_ADMIN_SECRET_KEY: str = "change_this_to_a_random_string"
    WEB_ADMIN_PASSWORD: str = "admin"

    SHORTLINK_ENABLED: bool = False
    SHORTLINK_API_URL: str = "https://api.shortlinkservice.com/api"
    SHORTLINK_API_KEY: str = ""

    @field_validator("DATABASE_URL", mode="before")
    def fix_database_url(cls, v: str) -> str:
        """Auto-fix Render's PostgreSQL URL to use asyncpg"""
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @property
    def admin_ids_list(self) -> List[int]:
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip()]

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    @property
    def webhook_full_url(self) -> str:
        return f"{self.WEBHOOK_URL.rstrip('/')}{self.WEBHOOK_PATH}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
