"""Thin HTTP client for Deepgram's token-grant endpoint.

We never expose the API key to the browser. /call/start mints a short-lived
JWT here and returns it to the client; the client uses it for the WebSocket
handshake via the Sec-WebSocket-Protocol header (only place browsers let
you put a custom header on a WS handshake).

Token TTL is capped server-side at 3600s by Deepgram; we use 300s by default
since the client only needs the token valid through WS upgrade.
"""

from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger(__name__)

GRANT_URL = "https://api.deepgram.com/v1/auth/grant"


class DeepgramError(Exception):
    """Raised when Deepgram returns a non-2xx from the grant endpoint."""


@dataclass
class GrantTokenResponse:
    access_token: str
    expires_in: int  # seconds


async def grant_token(
    api_key: str,
    ttl_seconds: int = 300,
    timeout: float = 5.0,
) -> GrantTokenResponse:
    """Mint a short-lived JWT for browser-side use.

    Args:
        api_key: server-side Deepgram API key (must have Member or higher).
        ttl_seconds: token lifetime; max 3600 enforced by Deepgram.
        timeout: HTTP timeout in seconds.

    Returns:
        GrantTokenResponse with access_token + expires_in.

    Raises:
        DeepgramError if the grant fails.
    """
    if not api_key:
        raise DeepgramError("DEEPGRAM_API_KEY is not configured")

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"ttl_seconds": ttl_seconds}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(GRANT_URL, headers=headers, json=payload)
    except httpx.HTTPError as e:
        logger.error("deepgram_grant_request_failed", error=str(e))
        raise DeepgramError(f"Could not reach Deepgram: {e}") from e

    if resp.status_code != 200:
        logger.error(
            "deepgram_grant_rejected",
            status=resp.status_code,
            body=resp.text[:500],
        )
        raise DeepgramError(
            f"Deepgram grant failed: {resp.status_code} {resp.text[:200]}"
        )

    body = resp.json()
    access_token = body.get("access_token") or body.get("token")
    expires_in = body.get("expires_in", ttl_seconds)
    if not access_token:
        raise DeepgramError(f"Deepgram grant returned no token: {body}")

    return GrantTokenResponse(access_token=access_token, expires_in=expires_in)
