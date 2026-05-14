"""Liveness probe — used by uptime monitors, NOT by container healthcheck.

DocuAI learned this the hard way: container HEALTHCHECK probes that hit
'/' on the API can return 5xx during boot before deps are ready, and
Coolify will restart the container in a loop. We disable the docker
healthcheck and use this route only for external monitoring.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "voicegen-api"}
