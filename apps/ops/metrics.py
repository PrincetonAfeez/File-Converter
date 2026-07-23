# "Operational metrics collection helpers."
from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import Count

from fileconverter.celery import app

logger = logging.getLogger(__name__)

# Queue names the worker consumes (kept in sync with docker-compose worker -Q).
MONITORED_QUEUES = ["default"]


def queue_depth(queue: str = "default") -> int | None:
    """Return the number of pending messages in a broker queue, or None if unavailable.

    Implemented for the Redis broker (LLEN of the queue key). Returns None for other
    brokers or when the broker is unreachable so callers can degrade gracefully.
    """
    try:
        with app.connection_or_acquire() as conn:
            channel = conn.default_channel
            client = getattr(channel, "client", None)
            if client is None or not hasattr(client, "llen"):
                return None
            return int(client.llen(queue))
    except Exception:
        logger.exception("Failed to read queue depth for %s", queue)
        return None


def job_status_counts() -> dict[str, int]:
    from apps.conversions.models import ConversionJob

    return {
        row["status"]: row["total"]
        for row in ConversionJob.objects.values("status").annotate(total=Count("id"))
    }


def dead_letter_count() -> int:
    from apps.conversions.models import JobEvent

    return JobEvent.objects.filter(event_type="job.dead_letter").count()


def outbox_failed_count() -> int:
    from apps.audit.models import OutboxEvent

    return OutboxEvent.objects.filter(failed_at__isnull=False).count()


def outbox_pending_count() -> int:
    from apps.audit.models import OutboxEvent

    return OutboxEvent.objects.filter(
        delivered_at__isnull=True, failed_at__isnull=True
    ).count()


def collect_metrics() -> dict:
    return {
        "queue_depth": {q: queue_depth(q) for q in MONITORED_QUEUES},
        "job_status_counts": job_status_counts(),
        "dead_letter_total": dead_letter_count(),
        "outbox_failed_total": outbox_failed_count(),
        "outbox_pending_total": outbox_pending_count(),
        "queue_backlog_threshold": settings.FILECONVERTER_QUEUE_BACKLOG_ALERT,
    }
