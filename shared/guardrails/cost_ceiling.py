"""Per-IP daily cost ceiling.

Rate limits cap REQUEST volume; this caps *units of work* (e.g., calls
started, USD spent). Lives in Redis as a per-(IP, YYYY-MM-DD) counter
with a 26h TTL.

For VoiceGen we use this two ways:
- Per-IP: `voicegen:calls` namespace counts calls/day/IP
- Global: `voicegen:cost_cents:global` counts ElevenLabs spend in cents
  across all IPs for the current UTC day. Once consumed, /call/start
  returns 503 — that's the cost ceiling.
"""

from datetime import UTC, datetime

from redis import Redis


def _today_key(ip: str, namespace: str) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{namespace}:cost:{ip}:{today}"


def cost_remaining(
    redis_client: Redis,
    ip: str,
    max_units: int,
    namespace: str = "voicegen",
) -> int:
    key = _today_key(ip, namespace)
    used = redis_client.get(key)
    used_int = int(used) if used else 0
    return max(0, max_units - used_int)


def consume_cost_units(
    redis_client: Redis,
    ip: str,
    units: int,
    max_units: int,
    namespace: str = "voicegen",
    ttl_seconds: int = 26 * 3600,
) -> bool:
    """Atomically reserve `units`. Returns True on success, False if it would
    exceed the daily ceiling."""
    if units <= 0:
        return True

    key = _today_key(ip, namespace)

    lua = """
    local current = tonumber(redis.call('GET', KEYS[1]) or '0')
    local incr = tonumber(ARGV[1])
    local max_units = tonumber(ARGV[2])
    local ttl = tonumber(ARGV[3])
    if current + incr > max_units then
        return 0
    end
    redis.call('INCRBY', KEYS[1], incr)
    redis.call('EXPIRE', KEYS[1], ttl)
    return 1
    """

    result = redis_client.eval(
        lua,
        1,
        key,
        str(units),
        str(max_units),
        str(ttl_seconds),
    )
    return int(result) == 1
