# "Tests for ops metrics helpers."
"""Tests for ops metrics helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.audit.models import OutboxEvent
from apps.conversions.models import ConversionJob, JobEvent
from apps.ops.metrics import (
    collect_metrics,
    dead_letter_count,
    job_status_counts,
    outbox_failed_count,
    outbox_pending_count,
    queue_depth,
)


def test_queue_depth_returns_none_when_broker_unavailable():
    with patch("apps.ops.metrics.app.connection_or_acquire", side_effect=RuntimeError("no broker")):
        assert queue_depth("default") is None


def test_queue_depth_reads_redis_llen():
    client = MagicMock()
    client.llen.return_value = 12
    channel = MagicMock(client=client)
    conn = MagicMock(default_channel=channel)
    with patch("apps.ops.metrics.app.connection_or_acquire") as acquire:
        acquire.return_value.__enter__.return_value = conn
        assert queue_depth("default") == 12


@pytest.mark.django_db
def test_job_status_counts(make_job):
    make_job("metrics-a", status=ConversionJob.Status.PENDING)
    make_job("metrics-b", status=ConversionJob.Status.DONE)
    counts = job_status_counts()
    assert counts[ConversionJob.Status.PENDING] >= 1
    assert counts[ConversionJob.Status.DONE] >= 1


@pytest.mark.django_db
def test_dead_letter_and_outbox_counts(make_job):
    job, _user, ws = make_job()
    JobEvent.objects.create(
        job=job,
        organization=ws.organization,
        event_type="job.dead_letter",
        message="boom",
    )
    OutboxEvent.objects.create(
        organization=ws.organization,
        event_type="job.failed",
        idempotency_key=f"failed-{job.pk}",
        payload={"job_id": job.pk},
        failed_at=job.created_at,
    )
    OutboxEvent.objects.create(
        organization=ws.organization,
        event_type="job.created",
        idempotency_key=f"pending-{job.pk}",
        payload={"job_id": job.pk},
    )
    assert dead_letter_count() >= 1
    assert outbox_failed_count() >= 1
    assert outbox_pending_count() >= 1


@pytest.mark.django_db
def test_collect_metrics_structure(make_job):
    make_job("metrics-collect")
    metrics = collect_metrics()
    assert "queue_depth" in metrics
    assert "job_status_counts" in metrics
    assert "outbox_pending_total" in metrics
