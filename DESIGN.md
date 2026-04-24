# DESIGN.md — Section 2: Rate-Limited Async Job Queue

## Problem Statement

Send transactional emails (order confirmations, OTP, alerts) with:
- A hard rate limit of 200 emails/minute imposed by the provider
- Burst handling: 2,000 requests in under 10 seconds during flash sales
- No job loss if a worker crashes mid-execution
- Retry with backoff on transient failures
- Dead-letter queue for permanently failed jobs

---

## Architecture Choice: Celery + Redis

### Options Considered

| Option | Pros | Cons |
|---|---|---|
| **Celery + Redis** | Mature ecosystem, built-in retry, rate limiting primitives, broad community support, acks_late for crash recovery | Operational overhead (broker + workers), Redis is in-memory (data loss on restart without persistence) |
| **Django-Q** | Simpler setup, uses Django ORM as broker option | Smaller community, ORM-as-broker has poor throughput under burst load, less mature retry/backoff control |
| **Custom (DB-backed queue)** | Full control, no external dependencies beyond DB | Must implement retry, concurrency, rate limiting, visibility timeout from scratch; error-prone; poor throughput |

### Decision: Celery + Redis

**Rationale:**
1. **Burst absorption:** Redis `LPUSH`/`BRPOP` handles 2,000 enqueues in milliseconds. A DB-backed queue would create 2,000 row inserts under contention.
2. **Crash recovery:** Celery's `acks_late=True` + `reject_on_worker_lost=True` provide message-level durability without custom implementation.
3. **Retry with backoff:** Celery's `self.retry(countdown=...)` with `max_retries` is battle-tested. Building exponential backoff with jitter on a custom queue requires careful state management.
4. **Production readiness:** Celery is used at Instagram, Mozilla, and Robinhood for exactly this class of problem.

**What we sacrifice:** Redis is in-memory by default. For true durability, we would enable Redis AOF persistence (`appendfsync everysec`) or use Redis Sentinel/Cluster. For this assessment, we assume a single Redis instance with AOF.

---

## Rate Limiter Design

### Approach: Sliding Window Log (Option B)

We use a **Redis sorted set** where each member is a unique request ID and its score is the timestamp. To check the rate limit:

1. `ZREMRANGEBYSCORE` — Remove entries older than `now - 60s`
2. `ZCARD` — Count remaining entries
3. If count < 200, `ZADD` the new entry; else reject

### Why Sliding Window Over Alternatives

**Token Bucket (Option A — DECR + TTL):**
Allows bursts up to the bucket capacity. If the bucket refills at 200/min but starts full, a burst of 200 can fire instantly. For email providers that enforce a strict per-minute cap (not per-second), this is acceptable — but if the provider measures on a rolling window, a burst at second 0 and another at second 60 could mean 400 in under 2 seconds. The token bucket doesn't prevent this boundary issue.

**Fixed Window (Option C — INCR + EXPIRE):**
Suffers from the classic boundary problem: 200 requests at 11:00:59 and 200 at 11:01:00 means 400 emails in 2 seconds, exceeding the provider's actual limit. Fixed windows are simpler but unsuitable when the provider enforces a true rolling window.

**Sliding Window Log (our choice):**
Tracks every request timestamp, so the count is always accurate for any rolling 60-second period. No boundary issues, no burst spikes beyond the limit. The trade-off is memory: we store one sorted-set member per request (200 entries max in the window — negligible for Redis).

### Atomicity Guarantee

The check-and-update (ZREMRANGEBYSCORE → ZCARD → ZADD) is wrapped in a **Lua script** executed via `redis.register_script()` / `EVAL`. Redis executes Lua atomically — no other command can interleave between the three operations. This prevents the race condition where two workers both read count=199 and both proceed to send.

We chose Lua over `MULTI/EXEC` because `MULTI/EXEC` does not support conditional logic (read-then-write). A pipeline would execute all commands regardless of the count check. Only Lua provides atomic conditional execution.

**Testing fallback:** The rate limiter auto-detects whether the Redis client supports Lua scripting (via a probe `EVAL "return 1" 0`). If Lua is unavailable (e.g., `fakeredis` on Windows without the `[lua]` extra), it falls back to direct Redis commands (ZREMRANGEBYSCORE, ZCARD, ZADD). This has a theoretical race condition under concurrency but is correct in single-threaded test environments. The Lua script remains the production path and is always used with a real Redis server.

### Redis Failure Mode: Fail Closed

If Redis is unavailable (connection timeout, crash), the rate limiter raises a `redis.ConnectionError`. The Celery task catches this and calls `self.retry(countdown=5)` — the job goes back to the queue and retries after 5 seconds.

**We deliberately fail closed** (block all sends) rather than fail open (send without limit). Reasoning: if we fail open during a flash sale burst of 2,000 emails, the provider would throttle or ban our API key, affecting ALL email delivery including OTPs and security alerts. Delaying emails by a few seconds while Redis recovers is far less damaging than losing the provider account.

---

## Retry Strategy

- **Exponential backoff with jitter:** `delay = 2^attempt * (1 + random(0, 0.5))`
- **Max retries:** 5 attempts (total time ~62 seconds worst case)
- **Dead-letter:** After 5 failures, the job is persisted to `DeadLetterJob` in PostgreSQL for manual investigation

The jitter prevents the **thundering herd problem**: if 100 tasks all fail at the same time (e.g., provider outage), without jitter they would all retry at exactly the same moment, causing another spike. Jitter spreads retries across a time window.

---

## SIGKILL Recovery

See ANSWERS.md for the detailed explanation. In summary:
- `acks_late=True` keeps messages in Redis until the task completes
- `reject_on_worker_lost=True` re-queues messages from dead workers
- Redis visibility timeout provides a final safety net
- Application-level idempotency keys prevent duplicate sends on re-delivery
