"""Worker service configuration — mirrors api.config with worker-only extras.

Kept separate so the worker doesn't pull in FastAPI imports at start-up.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str:
    if Path(".env").exists():
        return ".env"
    for path in [Path("../../.env"), Path("../../../.env")]:
        if path.exists():
            return str(path)
    return ".env"


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "voicegen-worker"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg2://voicegen:voicegen@localhost:5432/voicegen"
    redis_url: str = "redis://localhost:6379/0"

    opensearch_host: str = "localhost"
    opensearch_port: int = 9200
    opensearch_index: str = "voicegen_knowledge"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_vlm_model: str = "qwen/qwen2.5-vl-72b-instruct"

    discord_webhook_url: str = ""
    public_base_url: str = "http://localhost:8000"

    call_ttl_hours: int = 24
    data_dir: str = "/data"


settings = WorkerSettings()
