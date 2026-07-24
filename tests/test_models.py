# "Tests for domain model properties."
"""Tests for domain models and properties."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.conversions.models import ConversionJob
from apps.files.models import FileBlob


@pytest.mark.django_db
def test_conversion_job_is_terminal(make_job):
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)
    assert job.is_terminal is False
    job.status = ConversionJob.Status.DONE
    assert job.is_terminal is True


@pytest.mark.django_db
def test_output_downloadable_false_when_expired(make_job):
    job, _user, ws = make_job(status=ConversionJob.Status.DONE)
    out = FileBlob.objects.create(
        organization=ws.organization,
        workspace=ws,
        kind=FileBlob.Kind.OUTPUT,
        original_name="out.json",
    )
    job.output_file = out
    job.expires_at = timezone.now() - timezone.timedelta(minutes=1)
    assert job.output_downloadable is False


@pytest.mark.django_db
def test_output_downloadable_true_when_valid(make_job):
    job, _user, ws = make_job(status=ConversionJob.Status.DONE)
    out = FileBlob.objects.create(
        organization=ws.organization,
        workspace=ws,
        kind=FileBlob.Kind.OUTPUT,
        original_name="out.json",
    )
    job.output_file = out
    job.expires_at = timezone.now() + timezone.timedelta(hours=1)
    assert job.output_downloadable is True


@pytest.mark.django_db
def test_job_event_sets_organization_from_job(make_job):
    from apps.conversions.models import JobEvent

    job, _user, ws = make_job()
    event = JobEvent.objects.create(job=job, event_type="job.created", message="ok")
    assert event.organization_id == ws.organization_id
