# "Celery tasks for conversions and maintenance."
from __future__ import annotations

from functools import wraps

from django.core.cache import cache

from fileconverter.celery import app

from .services import (
    expire_due_outputs,
    garbage_collect_blobs,
    purge_terminal_input_files,
    requeue_stale_jobs,
    run_conversion,
)


def single_instance(lock_name: str, timeout: int = 600):
    """Ensure only one instance of a periodic task runs at a time across the fleet.

    Uses an atomic cache.add as a mutex so overlapping beat schedulers (or a slow run
    overlapping the next tick) do not double-execute. Requires a shared cache (Redis) for
    cross-process safety; degrades to per-process with LocMemCache.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = f"beatlock:{lock_name}"
            if not cache.add(key, "1", timeout):
                return 0
            try:
                return func(*args, **kwargs)
            finally:
                cache.delete(key)

        return wrapper

    return decorator


@app.task(bind=True, name="conversions.process_conversion_job", queue="default")
def process_conversion_job(self, job_pk: int) -> None:
    run_conversion(job_pk)


@app.task(name="conversions.reap_stale_jobs", queue="default")
@single_instance("reap_stale_jobs")
def reap_stale_jobs() -> int:
    return requeue_stale_jobs()


@app.task(name="conversions.expire_outputs", queue="default")
@single_instance("expire_outputs")
def expire_outputs() -> int:
    return expire_due_outputs()


@app.task(name="conversions.purge_input_files", queue="default")
@single_instance("purge_input_files")
def purge_input_files() -> int:
    return purge_terminal_input_files()


@app.task(name="conversions.gc_blobs", queue="default")
@single_instance("gc_blobs")
def gc_blobs() -> int:
    return garbage_collect_blobs()


@app.task(name="conversions.purge_quota_decisions", queue="default")
@single_instance("purge_quota_decisions")
def purge_quota_decisions() -> int:
    from apps.quotas.services import purge_expired_quota_decisions

    return purge_expired_quota_decisions()


@app.task(name="audit.deliver_outbox", queue="default")
@single_instance("deliver_outbox")
def deliver_outbox() -> int:
    from apps.audit.services import deliver_outbox_events

    return deliver_outbox_events()


@app.task(name="audit.purge_outbox", queue="default")
@single_instance("purge_outbox")
def purge_outbox() -> int:
    from apps.audit.services import purge_delivered_outbox_events, purge_failed_outbox_events

    return purge_delivered_outbox_events() + purge_failed_outbox_events()


@app.task(name="ops.monitor_queue_backlog", queue="default")
@single_instance("monitor_queue_backlog", timeout=60)
def monitor_queue_backlog() -> int:
    """Alert (WARNING log) when a broker queue backs up past the configured threshold."""
    import logging

    from django.conf import settings

    from apps.ops.metrics import MONITORED_QUEUES, queue_depth

    log = logging.getLogger("apps.ops")
    worst = 0
    for queue in MONITORED_QUEUES:
        depth = queue_depth(queue)
        if depth is None:
            continue
        worst = max(worst, depth)
        if depth >= settings.FILECONVERTER_QUEUE_BACKLOG_ALERT:
            log.warning(
                "QUEUE BACKLOG: %s has %s pending (threshold %s)",
                queue,
                depth,
                settings.FILECONVERTER_QUEUE_BACKLOG_ALERT,
            )
    return worst
