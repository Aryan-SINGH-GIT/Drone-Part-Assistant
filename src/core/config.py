from typing import Optional, Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Telegram Bot Token from @BotFather")
    TELEGRAM_DEFAULT_CHAT_ID: Optional[str] = Field(default=None, description="Default chat ID for stock alerts")

    # LLM Settings
    LLM_PROVIDER: Literal["gemini", "openai", "groq", "mock"] = Field(
        default="gemini", description="LLM provider for lightweight classification"
    )
    GEMINI_API_KEY: Optional[str] = Field(default=None)
    OPENAI_API_KEY: Optional[str] = Field(default=None)
    GROQ_API_KEY: Optional[str] = Field(default=None)

    # Monitoring & Alerting
    MONITORING_INTERVAL_HOURS: int = Field(default=24, description="Interval for scheduled scraping in hours")
    ALERT_COOLDOWN_HOURS: int = Field(default=6, description="Cooldown between duplicate alerts in hours")
    REQUEST_TIMEOUT_SECONDS: int = Field(default=15, description="Timeout for scraping HTTP requests")

    # Database
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///drone_assistant.db")
    LOG_LEVEL: str = Field(default="INFO")


settings = Settings()
