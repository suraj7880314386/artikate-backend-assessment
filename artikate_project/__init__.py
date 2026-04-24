# This ensures the Celery app is loaded when Django starts,
# so that @shared_task decorators use the correct app and broker.
from .celery import app as celery_app

__all__ = ("celery_app",)
