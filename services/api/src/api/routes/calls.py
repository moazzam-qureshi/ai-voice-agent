"""POST /call/start — Turnstile-gated, cost-capped, mints Deepgram JWT.

Single entry point for opening a voice call. The browser must call this
BEFORE opening its Deepgram WebSocket, then use the returned token and
settings_json to drive the WS handshake and the initial Settings message.

Guardrail order:
1. Turnstile token verification
2. Per-IP daily call count (max 2)
3. Global daily cost ceiling (USD cents — protects against credit burn)
4. Mint Deepgram JWT via grant_token
5. Create Call row in Postgres
6. Issue a per-call session token in Redis
7. Build the Settings JSON server-side and return everything

Failure mode mapping:
- Turnstile fail / rate limit → 403
- Cost ceiling hit → 503
- Deepgram grant fail → 502 (upstream)
"""

import structlog
from datetime import UTC, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.agent.prompts import GREETING, SYSTEM_PROMPT
from api.auth.call_session import issue_token
from api.config import settings
from api.db.redis_client import get_redis
from api.db.session import get_db
from shared.db_models import Call, CallStatus
from shared.deepgram import DeepgramError, build_agent_settings, grant_token
from shared.guardrails.client_ip import get_client_ip
from shared.guardrails.cost_ceiling import consume_cost_units
from shared.guardrails.turnstile import verify_turnstile_token

logger = structlog.get_logger(__name__)

router = APIRouter()


class CallStartRequest(BaseModel):
    turnstile_token: str = ""


class CallStartResponse(BaseModel):
    call_id: str
    deepgram_token: str
    deepgram_token_expires_in: int
    call_session_token: str
    settings_json: dict


# Per-call cost estimate in cents. A 90s call ≈ $0.30 = 30 cents. We
# reserve this much against the global ceiling at /call/start so a flood
# of concurrent starts can't race past the limit.
_PER_CALL_COST_ESTIMATE_CENTS = 30
_GLOBAL_COST_NAMESPACE = "voicegen:global_cost_cents"
_GLOBAL_COST_IP_KEY = "global"


@router.post("/call/start", response_model=CallStartResponse)
async def call_start(
    request: Request,
    body: CallStartRequest,
    db: AsyncSession = Depends(get_db),
) -> CallStartResponse:
    client_ip = get_client_ip(request)

    # 1. Turnstile gate
    ok = await verify_turnstile_token(
        token=body.turnstile_token,
        secret=settings.turnstile_secret,
        client_ip=client_ip,
    )
    if not ok:
        raise HTTPException(
            status_code=403,
            detail="Turnstile verification failed. Refresh and try again.",
        )

    redis = get_redis()

    # 2. Per-IP daily call count — atomic check-and-reserve. Two concurrent
    #    starts from the same IP can't both squeak past a TOCTOU window.
    ip_accepted = consume_cost_units(
        redis,
        ip=client_ip,
        units=1,
        max_units=settings.call_max_per_ip_per_day,
        namespace="voicegen:calls",
    )
    if not ip_accepted:
        raise HTTPException(
            status_code=429,
            detail=(
                f"You've hit the demo limit of {settings.call_max_per_ip_per_day} "
                "calls per day. Try again tomorrow, or get in touch for a "
                "self-hosted version."
            ),
        )

    # 3. Global daily cost ceiling (USD cents). ~30 cents per call estimate.
    max_cents = settings.global_daily_cost_usd_limit * 100
    accepted = consume_cost_units(
        redis,
        ip=_GLOBAL_COST_IP_KEY,
        units=_PER_CALL_COST_ESTIMATE_CENTS,
        max_units=max_cents,
        namespace=_GLOBAL_COST_NAMESPACE,
    )
    if not accepted:
        logger.warning(
            "global_cost_ceiling_hit",
            limit_usd=settings.global_daily_cost_usd_limit,
            client_ip=client_ip,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "The daily demo budget is exhausted. Try again after midnight UTC, "
                "or get in touch for a self-hosted version."
            ),
        )

    # 4. Mint the Deepgram JWT.
    # Note: the per-IP and global counters were already consumed above. If
    # grant_token fails here, we've burned 1 IP-call-slot + 30c of global
    # budget without an actual call. Acceptable failure mode for a portfolio
    # demo (Deepgram outages are rare); add a refund-on-error path if
    # production traffic ever justifies it.
    try:
        token_resp = await grant_token(
            api_key=settings.deepgram_api_key,
            ttl_seconds=settings.deepgram_grant_token_ttl_seconds,
        )
    except DeepgramError as e:
        logger.error("deepgram_grant_failed_in_call_start", error=str(e))
        raise HTTPException(status_code=502, detail="voice_provider_unavailable") from e

    # 6. Create the Call row
    expires_at = datetime.now(UTC) + timedelta(hours=settings.call_ttl_hours)
    call = Call(
        client_ip=client_ip,
        status=CallStatus.IN_PROGRESS.value,
        started_at=datetime.now(UTC),
        expires_at=expires_at,
    )
    db.add(call)
    await db.flush()
    call_id = call.id

    # 7. Issue the browser's per-call session token (Redis-backed)
    call_session_token = issue_token(call_id)

    # 8. Build the Settings JSON server-side so the prompt + function defs
    #    never appear in the JS bundle.
    settings_json = build_agent_settings(
        system_prompt=SYSTEM_PROMPT,
        greeting=GREETING,
        stt_model=settings.deepgram_stt_model,
        llm_provider=settings.deepgram_llm_provider,
        llm_model=settings.deepgram_llm_model,
        tts_model=settings.deepgram_tts_model,
    )

    logger.info(
        "call_started",
        call_id=call_id,
        client_ip=client_ip,
    )

    return CallStartResponse(
        call_id=call_id,
        deepgram_token=token_resp.access_token,
        deepgram_token_expires_in=token_resp.expires_in,
        call_session_token=call_session_token,
        settings_json=settings_json,
    )
