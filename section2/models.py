from django.db import models


class DeadLetterJob(models.Model):
    """
    Permanently failed jobs are recorded here instead of being silently dropped.
    This provides an audit trail and allows manual retry or investigation.
    """
    task_id = models.CharField(max_length=255, unique=True)
    task_name = models.CharField(max_length=255)
    args = models.JSONField(default=list)
    kwargs = models.JSONField(default=dict)
    exception = models.TextField()
    failed_at = models.DateTimeField(auto_now_add=True)
    retried = models.BooleanField(default=False)

    def __str__(self):
        return f"DeadLetter: {self.task_name} ({self.task_id})"
