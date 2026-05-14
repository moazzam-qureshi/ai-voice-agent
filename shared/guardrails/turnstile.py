"""Cloudflare Turnstile token verification.

Locked invariant for every project — see CLAUDE.md's Turnstile section.
For VoiceGen, the gate is on POST /call/start. Costs ~$0.45 per call,
so this is exactly the kind of expensive endpoint Turnstile is for.

Cloudflare siteverify docs:
https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
"""

import httpx
import structlog

logger = structlog.get_logger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile_token(
    token: str,
    secret: str,
    client_ip: str | None = None,
    timeout: float = 5.0,
) -> bool:
    """Verify a Turnstile token. Returns True if valid, False otherwise.

    Empty `secret` is the dev-mode escape hatch — returns True. Production
    deploys MUST set TURNSTILE_SECRET in Coolify env vars.
    """
    if not secret:
        logger.warning("turnstile_secret_missing_skipping_verification")
        return True

    if not token:
        return False

    data = {"secret": secret, "response": token}
    if client_ip:
        data["remoteip"] = client_ip

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(SITEVERIFY_URL, data=data)
            response.raise_for_status()
            payload = response.json()
    except Exception as e:
        logger.error("turnstile_verification_failed", error=str(e))
        return False

    success = bool(payload.get("success"))
    if not success:
        logger.warning(
            "turnstile_token_rejected",
            error_codes=payload.get("error-codes", []),
        )
    return success
