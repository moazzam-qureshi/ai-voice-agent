"""FastAPI app entry-point.

Wiring order matters:
1. Set the Dramatiq broker BEFORE importing any module that defines an actor.
2. Apply middleware (CORS, trusted-proxy) so request.state.client_ip exists.
3. Install slowapi limiter and exception handler.
4. Register routes.
"""

# ruff: noqa: I001  — `from api import broker` must precede route imports
# that transitively pull in shared.tasks actor decorations.

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

# IMPORTANT: import broker first so dramatiq.set_broker runs before any
# `@dramatiq.actor` decorators are evaluated via `shared.tasks` imports.
from api import broker  # noqa: F401
from api.config import settings
from api.routes import (
    admin,
    agent,
    artifacts,
    call_status,
    calls,
    health,
    recording,
    transcript,
)
from shared.guardrails.proxy import TrustedProxyMiddleware
from shared.guardrails.rate_limit import build_limiter, rate_limit_exceeded_response

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="VoiceGen AI",
    description=(
        "Voice customer-support agent for Moazzam's freelancing business. "
        "Deepgram Voice Agent + custom RAG over portfolio docs."
    ),
    version="0.1.0",
)

# === Middleware ===
# Trusted-proxy must run BEFORE rate-limiting so the limiter sees the real IP.
app.add_middleware(
    TrustedProxyMiddleware,
    trusted_proxies=settings.trusted_proxies,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo: open. Production tightens to the web origin.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Rate limiter ===
limiter = build_limiter(
    redis_url=settings.redis_url,
    default_limits=[
        f"{settings.rate_limit_per_hour}/hour",
        f"{settings.rate_limit_per_day}/day",
    ],
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return rate_limit_exceeded_response(request, exc)


# === Routes ===
app.include_router(health.router)
app.include_router(calls.router)        # POST /call/start
app.include_router(agent.router)        # POST /agent/search, /agent/wrap-up
app.include_router(transcript.router)   # POST /agent/transcript
app.include_router(recording.router)    # POST /calls/{call_id}/recording
app.include_router(call_status.router)  # GET  /calls/{call_id}
app.include_router(artifacts.router)    # GET  /artifacts/{download_token}
app.include_router(admin.router)        # POST /admin/knowledge, GET /admin/knowledge[/{id}]


@app.on_event("startup")
async def _startup() -> None:
    logger.info(
        "voicegen_api_starting",
        service=settings.service_name,
        log_level=settings.log_level,
        rate_limit_hour=settings.rate_limit_per_hour,
        rate_limit_day=settings.rate_limit_per_day,
        call_max_per_ip_per_day=settings.call_max_per_ip_per_day,
        call_max_seconds=settings.call_max_seconds,
        call_ttl_hours=settings.call_ttl_hours,
        global_daily_cost_cents=settings.global_daily_cost_usd_limit * 100,
        turnstile_enabled=bool(settings.turnstile_secret),
        deepgram_configured=bool(settings.deepgram_api_key),
        deepgram_llm=f"{settings.deepgram_llm_provider}/{settings.deepgram_llm_model}",
        deepgram_tts=settings.deepgram_tts_model,
    )
