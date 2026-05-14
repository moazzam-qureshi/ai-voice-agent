"""API service configuration loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str:
    """Find .env file, preferring project root over service dir."""
    if Path(".env").exists():
        return ".env"
    for path in [Path("../../.env"), Path("../../../.env")]:
        if path.exists():
            return str(path)
    return ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # === Service ===
    service_name: str = "voicegen-api"
    log_level: str = "INFO"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # === Persistence ===
    database_url: str = "postgresql+asyncpg://voicegen:voicegen@localhost:5432/voicegen"

    # === Queue / cache ===
    redis_url: str = "redis://localhost:6379/0"

    # === Hybrid search ===
    opensearch_host: str = "localhost"
    opensearch_port: int = 9200
    opensearch_index: str = "voicegen_knowledge"

    # === LLMs (for VLM knowledge-doc ingest only) ===
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_vlm_model: str = "qwen/qwen2.5-vl-72b-instruct"

    # === Deepgram Voice Agent ===
    deepgram_api_key: str = ""
    deepgram_llm_provider: str = "open_ai"
    deepgram_llm_model: str = "gpt-4o-mini"
    deepgram_tts_model: str = "aura-2-thalia-en"
    deepgram_stt_model: str = "flux-general-en"
    deepgram_grant_token_ttl_seconds: int = 300

    # === Guardrails ===
    trusted_proxies: str = "127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    rate_limit_per_hour: int = 30
    rate_limit_per_day: int = 100

    # Cloudflare Turnstile (required on /call/start).
    turnstile_secret: str = ""
    turnstile_sitekey: str = ""

    # === Call lifecycle ===
    call_max_per_ip_per_day: int = 2
    call_max_seconds: int = 90
    call_ttl_hours: int = 24
    # Per-call session token TTL in Redis. Browser uses this token on the
    # /agent/search and /agent/wrap-up endpoints — it lives only as long as
    # a call could possibly last.
    call_session_token_ttl_seconds: int = 180
    # Global daily ElevenLabs-equivalent spend ceiling, in USD cents.
    # Tracked atomically in Redis. /call/start refuses with 503 when hit.
    global_daily_cost_usd_limit: int = 10
    recording_max_bytes: int = 8 * 1024 * 1024  # 8 MB

    # === Discord ===
    discord_webhook_url: str = ""

    # === Public URL (artifact download links) ===
    public_base_url: str = "http://localhost:8000"

    # === Storage paths (mounted via docker volume) ===
    data_dir: str = "/data"

    # === Admin (knowledge-base management) ===
    # Bearer token required on all /admin/* endpoints. If empty, /admin/*
    # returns 403 (locked by default; explicit opt-in).
    admin_token: str = ""


settings = Settings()
