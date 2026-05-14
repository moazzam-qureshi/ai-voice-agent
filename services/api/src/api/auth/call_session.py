"""Per-call session tokens for browser → /agent/* authentication.

Why this exists: function-calling in Deepgram Voice Agent runs in the
browser (`client_side: true`). The browser receives a FunctionCallRequest
over the WebSocket, makes an HTTP call to our /agent/search or
/agent/wrap-up endpoint, and sends back a FunctionCallResponse. We need
to authenticate those HTTP calls without leaking a long-lived secret to
the client.

The pattern:
1. /call/start issues a random URL-safe token, stores `token -> call_id`
   in Redis with TTL slightly longer than the max call duration.
2. Browser sends the token in `X-Call-Session-Token` on every /agent/* hit.
3. Verify here: token present, lookup hits, returns the call_id.
4. The token dies on its own when the call expires — no logout needed.

Why not signed JWTs: this is simpler, requires no key rotation, and
gives us a kill-switch (`DELETE token` from Redis) if a session goes
rogue mid-call.
"""

import secrets

import structlog
from fastapi import Header, HTTPException

from api.config import settings
from api.db.redis_client import get_redis

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "voicegen:call_session:"


def issue_token(call_id: str) -> str:
    """Mint a token bound to a call_id. Returns the token; caller hands
    it to the browser."""
    token = secrets.token_urlsafe(32)
    redis = get_redis()
    redis.setex(
        f"{_KEY_PREFIX}{token}",
        settings.call_session_token_ttl_seconds,
        call_id,
    )
    return token


def revoke_token(token: str) -> None:
    """Kill a token immediately. Called after /agent/wrap-up so further
    /agent/* hits with the same token are rejected."""
    if not token:
        return
    redis = get_redis()
    redis.delete(f"{_KEY_PREFIX}{token}")


def _lookup(token: str) -> str | None:
    if not token:
        return None
    redis = get_redis()
    return redis.get(f"{_KEY_PREFIX}{token}")


def verify_call_session(
    x_call_session_token: str = Header(default=""),
) -> str:
    """FastAPI dependency: returns the call_id bound to the token, or 401.

    Mount with `Depends(verify_call_session)` on any /agent/* route.
    """
    call_id = _lookup(x_call_session_token)
    if not call_id:
        logger.warning("call_session_token_invalid", token_prefix=x_call_session_token[:8])
        raise HTTPException(status_code=401, detail="invalid_or_expired_session")
    return call_id


def verify_call_session_for_call_id(
    call_id: str,
    x_call_session_token: str = Header(default=""),
) -> str:
    """Variant that also asserts the token matches a specific call_id
    (used by /calls/{call_id}/recording where the path already names
    the call)."""
    bound_call_id = _lookup(x_call_session_token)
    if not bound_call_id or bound_call_id != call_id:
        logger.warning(
            "call_session_token_call_mismatch",
            token_prefix=x_call_session_token[:8],
            path_call_id=call_id,
        )
        raise HTTPException(status_code=401, detail="invalid_or_expired_session")
    return call_id
