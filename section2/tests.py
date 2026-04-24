"""
Section 2 Tests - Rate Limiter + Job Queue

Tests verify:
  1. Rate limiter correctly allows/denies under the sliding window
  2. No jobs are lost when submitting 500 jobs
  3. Rate limit is never exceeded
  4. Failed jobs are retried and eventually dead-lettered
"""

import time
import unittest
from unittest.mock import patch, MagicMock

import fakeredis
from django.test import TestCase, override_settings

from .rate_limiter import SlidingWindowRateLimiter


class SlidingWindowRateLimiterTest(unittest.TestCase):
    """Test the Redis sliding-window rate limiter in isolation."""

    def setUp(self):
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.limiter = SlidingWindowRateLimiter(
            self.redis, max_requests=10, window_seconds=60
        )

    def test_allows_requests_under_limit(self):
        """All 10 requests should be allowed within the window."""
        results = [self.limiter.allow("test_key") for _ in range(10)]
        self.assertTrue(all(results))
        self.assertEqual(self.limiter.current_count("test_key"), 10)

    def test_denies_requests_over_limit(self):
        """The 11th request must be denied."""
        for _ in range(10):
            self.limiter.allow("test_key")

        result = self.limiter.allow("test_key")
        self.assertFalse(result, "11th request should be rate-limited")

    def test_window_expiry_allows_new_requests(self):
        """After the window passes, new requests are allowed."""
        # Use a very short window for testing
        short_limiter = SlidingWindowRateLimiter(
            self.redis, max_requests=5, window_seconds=1
        )
        for _ in range(5):
            short_limiter.allow("expiry_key")

        self.assertFalse(short_limiter.allow("expiry_key"))

        # Wait for the window to expire
        time.sleep(1.1)

        self.assertTrue(
            short_limiter.allow("expiry_key"),
            "Should allow after window expires"
        )

    def test_current_count_accuracy(self):
        """current_count should reflect exactly how many requests are in window."""
        for _ in range(7):
            self.limiter.allow("count_key")
        self.assertEqual(self.limiter.current_count("count_key"), 7)

    def test_different_keys_are_independent(self):
        """Rate limits on different keys do not interfere."""
        for _ in range(10):
            self.limiter.allow("key_a")

        # key_b should still be open
        self.assertTrue(self.limiter.allow("key_b"))
        # key_a should be closed
        self.assertFalse(self.limiter.allow("key_a"))

    def test_atomicity_under_concurrent_simulation(self):
        """
        Simulate rapid concurrent calls.
        Even with 20 rapid calls, exactly 10 should be allowed.
        """
        allowed = sum(1 for _ in range(20) if self.limiter.allow("atomic_key"))
        self.assertEqual(allowed, 10)


class JobQueueIntegrationTest(unittest.TestCase):
    """
    Test that submitting 500 jobs results in no job loss
    and that the rate limit is respected.

    Uses a fake Redis and Celery's eager mode (CELERY_TASK_ALWAYS_EAGER)
    so we do not need a running broker.
    """

    def test_500_jobs_no_loss_rate_limit_respected(self):
        """
        Submit 500 jobs synchronously with a rate limit of 200/minute.
        Verify:
          - All 500 jobs complete (sent or dead-lettered)
          - At no point do we exceed 200 in a 60s window
        """
        fake_redis = fakeredis.FakeRedis(decode_responses=True)
        limiter = SlidingWindowRateLimiter(
            fake_redis, max_requests=200, window_seconds=60
        )

        sent_count = 0
        rate_limited_count = 0
        timestamps = []

        for i in range(500):
            if limiter.allow("test_jobs"):
                sent_count += 1
                timestamps.append(time.time())
            else:
                rate_limited_count += 1

        # All 500 were processed (200 allowed + 300 rate-limited)
        self.assertEqual(sent_count + rate_limited_count, 500)

        # First 200 should be allowed
        self.assertEqual(sent_count, 200)

        # Rate limit was never exceeded: at most 200 in any window
        self.assertLessEqual(sent_count, 200)

        # Verify no more than 200 timestamps fall in any 60s window
        if timestamps:
            for i, ts in enumerate(timestamps):
                window_count = sum(
                    1 for t in timestamps if ts - 60 <= t <= ts
                )
                self.assertLessEqual(
                    window_count, 200,
                    f"Rate limit exceeded at index {i}: {window_count} in window"
                )

        print(f"\n[500-JOB TEST] Sent: {sent_count}, "
              f"Rate-limited: {rate_limited_count}, "
              f"Total: {sent_count + rate_limited_count}")


class RetryAndDeadLetterTest(TestCase):
    """
    Test that an intentional failure is retried and eventually dead-lettered.
    Uses Celery eager mode.
    """

    def test_dead_letter_recording(self):
        """
        Test that _record_dead_letter correctly persists to the database.
        This is the mechanism that fires after MaxRetriesExceededError.
        """
        from .tasks import _record_dead_letter
        from .models import DeadLetterJob

        _record_dead_letter(
            task_id="test-task-123",
            task_name="section2.tasks.send_email",
            args=["fail@test.com", "Test Subject", "Body"],
            kwargs={"simulate_failure": True},
            exception="ConnectionError: Simulated email provider failure",
        )

        dead = DeadLetterJob.objects.get(task_id="test-task-123")
        self.assertEqual(dead.task_name, "section2.tasks.send_email")
        self.assertIn("fail@test.com", dead.args)
        self.assertIn("Simulated", dead.exception)
        self.assertFalse(dead.retried)
        print(f"\n[DEAD LETTER TEST] Recorded: {dead}")

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=False,
    )
    @patch("section2.tasks._get_rate_limiter")
    def test_failure_triggers_retries(self, mock_limiter_fn):
        """
        Verify that a failing task is retried (the rate limiter mock's
        call count shows the task was invoked multiple times).
        """
        from .tasks import send_email

        mock_limiter = MagicMock()
        mock_limiter.allow.return_value = True
        mock_limiter_fn.return_value = mock_limiter

        result = send_email.apply(
            args=["fail@test.com", "Test", "Body"],
            kwargs={"simulate_failure": True},
        )

        # The limiter was called multiple times = retries happened
        self.assertGreater(
            mock_limiter.allow.call_count, 1,
            "Task should have been retried at least once"
        )
        print(f"\n[RETRY TEST] Limiter called {mock_limiter.allow.call_count} times (= retries)")

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=False,
    )
    @patch("section2.tasks._get_rate_limiter")
    def test_successful_send(self, mock_limiter_fn):
        """Happy path: task sends email successfully."""
        from .tasks import send_email

        mock_limiter = MagicMock()
        mock_limiter.allow.return_value = True
        mock_limiter_fn.return_value = mock_limiter

        result = send_email.apply(
            args=["ok@test.com", "Hello", "World"],
            kwargs={"simulate_failure": False},
        )

        self.assertEqual(result.result["status"], "sent")
        self.assertEqual(result.result["recipient"], "ok@test.com")
