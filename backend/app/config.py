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
    # Large lecture decks and scanned exam compilations routinely exceed 60 MiB.
    # Uploads are streamed to disk, so accepting them does not reserve the whole
    # file in the API process memory.
    kb_max_file_mb: int = 100

    # Модель-архитектор промптов — работает в фоне, преподаватели её не видят и не выбирают.
    architect_base_url: str = ""
    architect_api_key: str = ""
    architect_model: str = "gpt-5.5"
    architect_family: str = "gpt"

    llm_request_timeout: float = 300.0
    data_dir: Path = Path("./data")

    # Only explicitly allowlisted models may make decisions that become grades,
    # validated tasks or student-facing tutor replies. Unlisted models are safe
    # by default: they remain available only for an explicit preview.
    model_use_policy_version: str = "model-use-v1"
    decision_model_ids: str = "deepseek-v4-pro"
    advisory_model_ids: str = "deepseek-v4-flash"

    # Server-to-server publication into the stable Picrete runtime.
    picrete_api_url: str = ""
    picrete_integration_token: str = ""

    # S3 (Yandex Object Storage). Пустой endpoint = выключено, всё живёт на локальном диске.
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = ""
    s3_region: str = "ru-central1"
    s3_prefix: str = "studio/"

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
