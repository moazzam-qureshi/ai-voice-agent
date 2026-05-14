"""Singleton sync Redis client for the API.

Used by:
- Cost-ceiling Lua script in shared.guardrails.cost_ceiling
- Per-call session tokens (issued by /call/start, verified by /agent/*)

We use the sync client even from async endpoints because the per-call
operations are sub-millisecond and not worth the asyncio import surface.
"""

from functools import lru_cache

from redis import Redis

from api.config import settings


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)
