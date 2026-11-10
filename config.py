from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = ""
    admin_id: int = 0
    webapp_url: str = "https://example.com"
    database_url: str = "sqlite+aiosqlite:///./database.db"
    stars_per_spin: int = 50
    paid_wheel_enabled: bool = False
    quest_spin_cost: int = 100
    referrals_per_free_spin: int = 3
    points_per_referral: int = 10
    points_for_channel: int = 50
    required_channel: str = ""
    channel_url: str = ""
    init_data_max_age_seconds: int = 86400
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and ":" in self.bot_token and self.admin_id > 0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
