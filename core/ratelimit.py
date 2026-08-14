"""Redis-backed rate limiting and concurrency control.

The limiter is a sliding-window counter evaluated inside a Lua script, so the
read-modify-write is atomic across every API replica. Concurrency slots use a
separate counter with a TTL, so a worker that dies without releasing its slot
does not wedge the organisation forever.
"""

from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as redis

# Sliding window: drop entries older than the window, count what is left, and
# only then admit the caller. Returning the count lets us emit useful headers.
_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local used = redis.call('ZCARD', key)
if used >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local reset = window
  if oldest[2] then reset = math.ceil((tonumber(oldest[2]) + window) - now) end
  return {0, used, reset}
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window))
return {1, used + 1, 0}
"""

# Acquire a concurrency slot only if the org is below its ceiling.
_ACQUIRE_SLOT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local current = tonumber(redis.call('GET', key) or '0')
if current >= limit then
  return {0, current}
end
local value = redis.call('INCR', key)
redis.call('EXPIRE', key, ttl)
return {1, value}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    used: int
    retry_after_seconds: int

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)


class RateLimiter:
    def __init__(self, client: redis.Redis) -> None:
        self._redis = client
        self._window_script = client.register_script(_SLIDING_WINDOW)
        self._slot_script = client.register_script(_ACQUIRE_SLOT)

    async def check(
        self, bucket: str, *, limit: int, window_seconds: int = 60
    ) -> RateLimitResult:
        import time
        import uuid

        now = time.time()
        allowed, used, reset = await self._window_script(
            keys=[f"rl:{bucket}"],
            args=[now, window_seconds, limit, f"{now}:{uuid.uuid4().hex[:8]}"],
        )
        return RateLimitResult(
            allowed=bool(allowed),
            limit=limit,
            used=int(used),
            retry_after_seconds=int(reset),
        )

    async def acquire_slot(self, bucket: str, *, limit: int, ttl_seconds: int) -> bool:
        """Take one concurrency slot. The TTL is a safety net for crashed holders."""
        acquired, _ = await self._slot_script(
            keys=[f"slots:{bucket}"], args=[limit, ttl_seconds]
        )
        return bool(acquired)

    async def release_slot(self, bucket: str) -> None:
        key = f"slots:{bucket}"
        # DECR can go negative if a slot was reclaimed by TTL while still held;
        # clamp at zero rather than letting the counter drift below it.
        value = await self._redis.decr(key)
        if value < 0:
            await self._redis.set(key, 0)

    async def slots_in_use(self, bucket: str) -> int:
        value = await self._redis.get(f"slots:{bucket}")
        return int(value or 0)
