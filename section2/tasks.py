import logging
import random
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_rate_limiter():
    import redis as redis_lib
    from .rate_limiter import SlidingWindowRateLimiter
    client = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return SlidingWindowRateLimiter(
        client,
        max_requests=settings.EMAIL_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )


@shared_task(
    bind=True,
    max_retries=5,
    acks_late=True,
    reject_on_worker_lost=True,
    serializer="json",
)
def send_email(self, recipient, subject, body, simulate_failure=False):
    task_id = self.request.id

    # Step 1: Check rate limit
    try:
        limiter = _get_rate_limiter()
        if not limiter.allow("rate_limit:email_send"):
            logger.info("[%s] Rate-limited, retry in 1s", task_id)
            raise self.retry(countdown=1, max_retries=20)
    except Exception as exc:
        if "Retry" in type(exc).__name__ or isinstance(exc, MaxRetriesExceededError):
            raise
        logger.error("[%s] Rate limiter unavailable: %s", task_id, exc)
        raise self.retry(exc=exc, countdown=5)

    # Step 2: Simulate sending
    try:
        if simulate_failure:
            raise ConnectionError("Simulated email provider failure")
        logger.info("[%s] Email sent to %s: %s", task_id, recipient, subject)
        return {"status": "sent", "recipient": recipient, "task_id": task_id}
    except Exception as exc:
        retry_number = self.request.retries
        backoff = (2 ** retry_number) * (1 + random.uniform(0, 0.5))
        logger.warning("[%s] Failed attempt %d/5: %s. Retry in %.1fs",
                       task_id, retry_number + 1, exc, backoff)
        try:
            raise self.retry(exc=exc, countdown=backoff)
        except MaxRetriesExceededError:
            _record_dead_letter(
                task_id=task_id or "unknown",
                task_name="section2.tasks.send_email",
                args=[recipient, subject, body],
                kwargs={"simulate_failure": simulate_failure},
                exception=str(exc),
            )
            logger.error("[%s] Permanently failed. Dead-lettered.", task_id)
            return {"status": "dead_lettered", "recipient": recipient}


def _record_dead_letter(task_id, task_name, args, kwargs, exception):
    from .models import DeadLetterJob
    DeadLetterJob.objects.create(
        task_id=task_id,
        task_name=task_name,
        args=args,
        kwargs=kwargs,
        exception=exception,
    )
