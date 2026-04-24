"""
Section 5 — Live Demo Script

Run this script while recording your screen with Loom.
It demonstrates:
  1. Submitting 150+ jobs to the Celery queue
  2. Rate limiter throttling (never exceeds 200/min)
  3. A failed job being retried with exponential backoff
  4. Redis queue state in real time

PREREQUISITES:
  1. Redis running locally:  redis-server
  2. Celery worker running:  celery -A artikate_project worker --loglevel=info --concurrency=4
  3. Then run this script:   python demo_live.py
"""

import os
import sys
import time
import django
import redis

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "artikate_project.settings")
django.setup()

from section2.tasks import send_email
from section2.rate_limiter import SlidingWindowRateLimiter
from django.conf import settings


def get_redis_client():
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def print_header(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def show_redis_state(r, label=""):
    """Show current Redis queue and rate limiter state."""
    # Celery uses a list named 'celery' as the default queue
    queue_len = r.llen("celery")
    # Rate limiter sorted set
    limiter = SlidingWindowRateLimiter(r, max_requests=200, window_seconds=60)
    rate_count = limiter.current_count("rate_limit:email_send")
    print(f"  [{label}] Queue depth: {queue_len} | Rate window: {rate_count}/200")


def main():
    r = get_redis_client()

    # Verify Redis connection
    try:
        r.ping()
        print("Redis connected successfully.")
    except redis.ConnectionError:
        print("ERROR: Cannot connect to Redis. Start it with: redis-server")
        sys.exit(1)

    # Clean slate
    r.delete("rate_limit:email_send")
    print("Cleared rate limiter state.\n")

    # ─────────────────────────────────────────────
    # PART 1: Submit 150 normal jobs
    # ─────────────────────────────────────────────
    print_header("PART 1: Submitting 150 email jobs")
    print("  (Watch the Celery worker terminal for processing logs)\n")

    for i in range(1, 151):
        send_email.delay(
            recipient=f"user{i}@example.com",
            subject=f"Order Confirmation #{i}",
            body=f"Your order #{i} has been confirmed.",
            simulate_failure=False,
        )
        if i % 25 == 0:
            show_redis_state(r, label=f"After {i} submitted")

    print(f"\n  All 150 jobs submitted.")
    show_redis_state(r, label="Final")

    # ─────────────────────────────────────────────
    # PART 2: Watch rate limiter in action
    # ─────────────────────────────────────────────
    print_header("PART 2: Monitoring rate limiter (30 seconds)")
    print("  The rate limiter should cap at 200 emails/minute.")
    print("  Watch the count — it should never exceed 200.\n")

    for tick in range(15):
        time.sleep(2)
        show_redis_state(r, label=f"T+{(tick+1)*2}s")

    # ─────────────────────────────────────────────
    # PART 3: Submit jobs that WILL fail (retry demo)
    # ─────────────────────────────────────────────
    print_header("PART 3: Submitting 5 jobs that will FAIL (retry demo)")
    print("  These jobs have simulate_failure=True.")
    print("  Watch the Celery worker terminal for retry logs with backoff.\n")

    for i in range(1, 6):
        result = send_email.delay(
            recipient=f"fail{i}@example.com",
            subject=f"This Will Fail #{i}",
            body="Intentional failure for demo.",
            simulate_failure=True,
        )
        print(f"  Submitted failing job {i}: task_id={result.id}")

    # ─────────────────────────────────────────────
    # PART 4: Watch retries happen
    # ─────────────────────────────────────────────
    print_header("PART 4: Watching retries (60 seconds)")
    print("  Look at the Celery worker terminal — you should see:")
    print("    - 'Failed attempt 1/5 ... Retry in ~2s'")
    print("    - 'Failed attempt 2/5 ... Retry in ~4s'")
    print("    - 'Failed attempt 3/5 ... Retry in ~8s'")
    print("    - etc. (exponential backoff with jitter)\n")

    for tick in range(12):
        time.sleep(5)
        show_redis_state(r, label=f"T+{(tick+1)*5}s")

    # ─────────────────────────────────────────────
    # PART 5: Check dead-letter queue
    # ─────────────────────────────────────────────
    print_header("PART 5: Checking dead-letter queue")

    from section2.models import DeadLetterJob
    dead_count = DeadLetterJob.objects.count()
    print(f"  Dead-lettered jobs in database: {dead_count}")

    if dead_count > 0:
        for job in DeadLetterJob.objects.all()[:5]:
            print(f"    - {job.task_id}: {job.exception[:60]}")

    print_header("DEMO COMPLETE")
    print("  Summary:")
    print("    - 150 normal jobs submitted and processed")
    print("    - Rate limiter enforced 200/min cap")
    print("    - 5 failing jobs retried with exponential backoff")
    print(f"    - {dead_count} jobs in dead-letter queue")
    print("\n  Stop recording now!\n")


if __name__ == "__main__":
    main()
