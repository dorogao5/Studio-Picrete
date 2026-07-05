from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="STUDIO_", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./data/studio.db"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 60 * 24 * 7

    first_admin_username: str = "admin"
    first_admin_password: str = "admin"

    cors_origins: str = "http://localhost:5174,https://dev.picrete.com"

    datalab_api_key: str = ""
    datalab_base_url: str = "https://www.datalab.to/api/v1"
    datalab_mode: str = "accurate"
    datalab_poll_interval_seconds: float = 2.0
    datalab_max_poll_attempts: int = 120
    # Документы базы знаний могут быть большими (главы учебников) — ждём дольше.
    datalab_kb_max_poll_attempts: int = 600
    kb_max_file_mb: int = 60

    # Модель-архитектор промптов — работает в фоне, преподаватели её не видят и не выбирают.
    architect_base_url: str = ""
    architect_api_key: str = ""
    architect_model: str = "gpt-5.5"
    architect_family: str = "gpt"

    llm_request_timeout: float = 300.0
    data_dir: Path = Path("./data")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def kb_dir(self) -> Path:
        return self.data_dir / "kb"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.kb_dir.mkdir(parents=True, exist_ok=True)
    return settings
