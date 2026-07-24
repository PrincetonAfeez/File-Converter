# "Tests for Celery tasks and locking."
"""Tests for Celery tasks and single-instance locking."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.conversions.models import ConversionJob
from apps.conversions.tasks import (
    deliver_outbox,
    gc_blobs,
    monitor_queue_backlog,
    process_conversion_job,
    purge_outbox,
    purge_quota_decisions,
    reap_stale_jobs,
    single_instance,
)


@pytest.mark.django_db
def test_process_conversion_job_runs_conversion(make_job):
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)
    with patch("apps.conversions.tasks.run_conversion") as run:
        process_conversion_job(job.pk)
        run.assert_called_once_with(job.pk)


@pytest.mark.django_db
def test_reap_stale_jobs_delegates():
    cache.clear()
    with patch("apps.conversions.tasks.requeue_stale_jobs", return_value=2) as requeue:
        assert reap_stale_jobs() == 2
        requeue.assert_called_once()


def test_single_instance_skips_when_lock_held():
    cache.clear()
    cache.add("beatlock:held", "1", 60)

    @single_instance("held")
    def worker():
        raise AssertionError("should not run")

    assert worker() == 0


def test_single_instance_runs_and_releases_lock():
    cache.clear()

    @single_instance("free")
    def worker():
        return 7

    assert worker() == 7
    assert cache.get("beatlock:free") is None


@pytest.mark.django_db
def test_gc_blobs_task_returns_count():
    cache.clear()
    with patch("apps.conversions.tasks.garbage_collect_blobs", return_value=3):
        assert gc_blobs() == 3


@pytest.mark.django_db
def test_purge_quota_decisions_task():
    cache.clear()
    with patch(
        "apps.quotas.services.purge_expired_quota_decisions", return_value=4
    ) as purge:
        assert purge_quota_decisions() == 4
        purge.assert_called_once()


@pytest.mark.django_db
def test_deliver_and_purge_outbox_tasks():
    cache.clear()
    with patch("apps.audit.services.deliver_outbox_events", return_value=1):
        assert deliver_outbox() == 1
    with patch(
        "apps.audit.services.purge_delivered_outbox_events", return_value=2
    ), patch("apps.audit.services.purge_failed_outbox_events", return_value=1):
        assert purge_outbox() == 3


@pytest.mark.django_db
def test_monitor_queue_backlog_logs_when_deep(settings):
    cache.clear()
    settings.FILECONVERTER_QUEUE_BACKLOG_ALERT = 5
    with patch("apps.ops.metrics.queue_depth", return_value=9):
        assert monitor_queue_backlog() == 9
