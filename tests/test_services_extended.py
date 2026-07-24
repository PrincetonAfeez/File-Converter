# "Extended conversion service unit tests."
"""Extended tests for conversion services."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.conversions.models import ConversionJob
from apps.conversions.services import (
    _idempotent_job_returnable,
    _verify_idempotent_match,
    claim_job,
    heartbeat,
    raise_if_cancelled,
    requeue_stale_jobs,
    request_cancel,
    run_conversion,
    submit_conversion_job,
)
from apps.organizations.models import Membership


@pytest.mark.django_db
def test_verify_idempotent_match_detects_format_mismatch(make_job):
    job, _user, _ws = make_job()
    with pytest.raises(ValueError, match="different conversion target"):
        _verify_idempotent_match(
            job,
            source_format="png",
            target_format="json",
            upload_checksum="",
            options={},
        )


@pytest.mark.django_db
def test_verify_idempotent_match_detects_options_mismatch(make_job):
    job, _user, _ws = make_job()
    job.option_payload = {"quality": 90}
    job.save(update_fields=["option_payload"])
    with pytest.raises(ValueError, match="different conversion options"):
        _verify_idempotent_match(
            job,
            source_format="csv",
            target_format="json",
            upload_checksum="",
            options={"quality": 80},
        )


@pytest.mark.django_db
def test_idempotent_job_returnable_states(make_job):
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)
    assert _idempotent_job_returnable(job) is True
    job.status = ConversionJob.Status.FAILED
    assert _idempotent_job_returnable(job) is False


@pytest.mark.django_db
def test_verify_idempotent_match_detects_mismatch(make_job):
    job, _user, _ws = make_job()
    job.input_checksum = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    job.save(update_fields=["input_checksum"])
    with pytest.raises(ValueError, match="different file"):
        _verify_idempotent_match(
            job,
            source_format="csv",
            target_format="json",
            upload_checksum="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            options={},
        )


@pytest.mark.django_db
def test_claim_job_increments_generation(make_job):
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)
    result = claim_job(job.pk, worker_id="w1")
    assert result is not None
    claimed, generation = result
    assert generation == 1
    assert claimed.status == ConversionJob.Status.PROCESSING


@pytest.mark.django_db
def test_heartbeat_updates_processing_job(make_job):
    job, _user, _ws = make_job(status=ConversionJob.Status.PROCESSING, claim_generation=2)
    assert heartbeat(job.pk, 2) is True
    assert heartbeat(job.pk, 999) is False


@pytest.mark.django_db
def test_raise_if_cancelled(make_job):
    job, _user, _ws = make_job(status=ConversionJob.Status.PROCESSING, claim_generation=1)
    from apps.conversions.services import JobCancelled

    ConversionJob.objects.filter(pk=job.pk).update(cancel_requested=True)
    with pytest.raises(JobCancelled):
        raise_if_cancelled(job.pk, 1)


@pytest.mark.django_db
def test_requeue_stale_cancel_requested(settings, make_job):
    job, _user, _ws = make_job(status=ConversionJob.Status.PROCESSING, claim_generation=1)
    ConversionJob.objects.filter(pk=job.pk).update(
        cancel_requested=True,
        heartbeat_at=timezone.now() - timezone.timedelta(minutes=30),
    )
    requeue_stale_jobs()
    job.refresh_from_db()
    assert job.status == ConversionJob.Status.CANCELLED


@pytest.mark.django_db
def test_run_conversion_unsupported_pair_fails_cleanly(settings, make_job):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    job, _user, _ws = make_job(status=ConversionJob.Status.PENDING)
    ConversionJob.objects.filter(pk=job.pk).update(source_format="nope", target_format="nope")
    run_conversion(job.pk)
    job.refresh_from_db()
    assert job.status == ConversionJob.Status.FAILED


@pytest.mark.django_db
def test_auditor_cannot_submit(settings, shared_org, csv_upload):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    org, ws, _owner, member = shared_org
    Membership.objects.filter(user=member, organization=org).update(
        role=Membership.Role.AUDITOR
    )
    with pytest.raises(ValueError, match="permission"):
        submit_conversion_job(
            user=member,
            workspace=ws,
            uploaded_file=csv_upload,
            target_format="json",
            idempotency_key="aud-submit",
            options={},
        )


@pytest.mark.django_db
def test_immediate_cancel_pending(make_job):
    job, user, _ws = make_job(status=ConversionJob.Status.PENDING)
    request_cancel(job, actor=user)
    job.refresh_from_db()
    assert job.status == ConversionJob.Status.CANCELLED
