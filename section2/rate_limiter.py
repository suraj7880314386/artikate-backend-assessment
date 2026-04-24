"""
Rate Limiter — Redis Sliding Window Log Implementation

Uses a Redis sorted set to track timestamps of each operation within
the current window. A Lua script ensures atomicity of the
check-and-update cycle.

Why sliding window over token bucket or fixed window:
-----------------------------------------------------
- Token bucket: Allows bursts up to the bucket size, which could
  momentarily exceed the provider's per-minute limit.
- Fixed window: Suffers from the boundary problem — 200 requests at
  :59 and 200 at :00 means 400 in 2 seconds, violating the limit.
- Sliding window log: Provides the most accurate enforcement because
  it considers the exact timestamps of every request within a rolling
  60-second window. No boundary issues, no burst spikes.

Atomicity guarantee:
--------------------
PRODUCTION (Lua available): The entire check-and-update is wrapped
in a Lua script executed via EVAL. Redis executes Lua scripts
atomically — no other command can interleave between
ZREMRANGEBYSCORE, ZCARD, and ZADD. This prevents race conditions
where two workers both read count=199 and both proceed.

TESTING (pipeline fallback): When Lua is not available (e.g.,
fakeredis without the [lua] extra on Windows), we fall back to
direct Redis commands. This is acceptable for single-threaded
testing but NOT for production. In production, always use a Redis
server that supports EVAL (all standard Redis versions do).

Failure mode:
-------------
If Redis is unavailable, the rate limiter raises an exception
(fail CLOSED). This is intentional: if we fail open, a burst of
2000 emails could hit the provider and get us throttled or banned.
Failing closed means jobs stay in the Celery queue and are retried
when Redis recovers.
"""

import time
import logging

logger = logging.getLogger(__name__)

# Lua script for atomic sliding-window rate limiting.
# KEYS[1] = the sorted set key
# ARGV[1] = window start (now - window_seconds)
# ARGV[2] = current timestamp (float)
# ARGV[3] = max allowed requests in window
# ARGV[4] = unique member ID (to avoid duplicates in the set)
#
# Returns 1 if allowed, 0 if rate-limited.
SLIDING_WINDOW_LUA = """
-- Remove entries older than the window
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])

-- Count current entries in the window
local current_count = redis.call('ZCARD', KEYS[1])

if current_count < tonumber(ARGV[3]) then
    -- Under limit: add this request and allow
    redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])
    -- Set TTL on the key so it auto-expires (window_seconds + buffer)
    redis.call('EXPIRE', KEYS[1], 120)
    return 1
else
    -- Over limit: deny
    return 0
end
"""


class SlidingWindowRateLimiter:
    """
    A Redis-backed sliding window rate limiter.

    Usage:
        limiter = SlidingWindowRateLimiter(redis_client, max_requests=200, window_seconds=60)
        if limiter.allow("email_send"):
            send_email(...)
        else:
            # Retry later
    """

    def __init__(self, redis_client, max_requests=200, window_seconds=60):
        self.redis = redis_client
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lua_available = self._check_lua_support()

        if self._lua_available:
            self._script = self.redis.register_script(SLIDING_WINDOW_LUA)

    def _check_lua_support(self):
        """
        Detect whether the Redis client supports Lua scripting.
        Real Redis always does; fakeredis only does with the [lua] extra
        (which requires lupa, a C extension that can be hard to install
        on Windows).
        """
        try:
            self.redis.eval("return 1", 0)
            return True
        except Exception:
            logger.info(
                "Lua scripting not available -- using pipeline fallback. "
                "This is fine for testing but NOT for production."
            )
            return False

    def allow(self, key="rate_limit:email"):
        """
        Check if a request is allowed under the rate limit.
        Returns True if allowed, False if rate-limited.
        Raises redis.ConnectionError if Redis is down (fail closed).
        """
        if self._lua_available:
            return self._allow_lua(key)
        return self._allow_pipeline(key)

    def _allow_lua(self, key):
        """Atomic check-and-update using Lua script (production path)."""
        now = time.time()
        window_start = now - self.window_seconds
        member_id = f"{now}:{id(self)}"

        result = self._script(
            keys=[key],
            args=[window_start, now, self.max_requests, member_id],
        )
        return result == 1

    def _allow_pipeline(self, key):
        """
        Fallback for environments without Lua support (e.g., fakeredis
        on Windows without the [lua] extra).

        Performs the same sliding-window logic using direct Redis
        commands. In a single-threaded test environment, this behaves
        identically to the Lua script.

        In production with multiple concurrent workers, this has a
        small race window between ZCARD and ZADD where two workers
        could both read count=199 and both proceed. The Lua script
        eliminates this race. This fallback exists ONLY so tests pass
        on all platforms.
        """
        now = time.time()
        window_start = now - self.window_seconds
        member_id = f"{now}:{id(self)}"

        # Step 1: Clean expired entries
        self.redis.zremrangebyscore(key, "-inf", window_start)

        # Step 2: Check current count
        current_count = self.redis.zcard(key)

        if current_count < self.max_requests:
            # Under limit: add this request
            self.redis.zadd(key, {member_id: now})
            self.redis.expire(key, self.window_seconds * 2)
            return True
        else:
            # Over limit: deny
            return False

    def current_count(self, key="rate_limit:email"):
        """Return the number of requests in the current window (for monitoring)."""
        now = time.time()
        window_start = now - self.window_seconds
        self.redis.zremrangebyscore(key, "-inf", window_start)
        return self.redis.zcard(key)
