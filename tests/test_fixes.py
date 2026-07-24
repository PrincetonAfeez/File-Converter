# "Regression tests for hardening fixes."
"""Tests for audit fixes (outbox dead-letter, empty uploads, idempotency, suspended org)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.audit.models import OutboxEvent
from apps.audit.services import deliver_outbox_events
from apps.conversions.services import submit_conversion_job
from apps.files.utils import validate_upload_size
from apps.organizations.models import Organization
from apps.organizations.services import ensure_personal_workspace


@pytest.mark.django_db
def test_idempotent_mismatch_rejected(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    user = get_user_model().objects.create_user(username="idem-mismatch", password="pw")
    workspace = ensure_personal_workspace(user)

    def submit(key, body):
        upload = SimpleUploadedFile("d.csv", body, content_type="text/csv")
        return submit_conversion_job(
            user=user,
            workspace=workspace,
            uploaded_file=upload,
            target_format="json",
            idempotency_key=key,
            options={},
        )

    submit("SAME", b"a,b\n1,2\n")
    with pytest.raises(ValueError, match="different file"):
        submit("SAME", b"x,y\n9,9\n")


def test_empty_upload_rejected():
    upload = SimpleUploadedFile("empty.csv", b"", content_type="text/csv")
    with pytest.raises(ValueError, match="empty"):
        validate_upload_size(upload)


@pytest.mark.django_db
def test_suspended_org_cannot_submit(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    user = get_user_model().objects.create_user(username="suspended-user", password="pw")
    workspace = ensure_personal_workspace(user)
    Organization.objects.filter(pk=workspace.organization_id).update(
        status=Organization.Status.SUSPENDED
    )
    workspace.organization.refresh_from_db()

    with pytest.raises(ValueError, match="suspended"):
        submit_conversion_job(
            user=user,
            workspace=workspace,
            uploaded_file=SimpleUploadedFile("d.csv", b"a,b\n1,2\n", content_type="text/csv"),
            target_format="json",
            idempotency_key="susp1",
            options={},
        )


@pytest.mark.django_db
def test_outbox_marks_failed_after_max_attempts(settings, monkeypatch):
    from apps.audit import services as audit_services

    org = Organization.objects.create(name="Outbox Fail Co")
    event = audit_services.enqueue_outbox(
        organization=org,
        event_type="conversion.job.done",
        idempotency_key="fail-me",
        payload={"x": 1},
    )
    settings.FILECONVERTER_WEBHOOK_URL = "https://hooks.example/ingest"
    settings.FILECONVERTER_OUTBOX_MAX_ATTEMPTS = 1

    def boom(*args, **kwargs):
        raise RuntimeError("delivery down")

    monkeypatch.setattr(audit_services, "_deliver_one", boom)
    assert deliver_outbox_events() == 0

    event.refresh_from_db()
    assert event.failed_at is not None
    assert OutboxEvent.objects.filter(
        event_type="outbox.event.dead_letter",
        idempotency_key=f"outbox:{event.public_id}:dead_letter",
    ).exists()


@pytest.mark.django_db
def test_user_with_active_org_can_login_despite_other_suspended_org(client):
    from apps.organizations.models import Membership, Organization, Workspace

    active_org = Organization.objects.create(name="Active Co", status=Organization.Status.ACTIVE)
    suspended_org = Organization.objects.create(
        name="Suspended Co", status=Organization.Status.SUSPENDED
    )
    user = get_user_model().objects.create_user(username="multi-org", password="goodpw")
    Membership.objects.create(user=user, organization=active_org, role=Membership.Role.MEMBER)
    Membership.objects.create(user=user, organization=suspended_org, role=Membership.Role.MEMBER)
    Workspace.objects.create(organization=active_org, name="Main")

    assert client.login(username="multi-org", password="goodpw") is True


@pytest.mark.django_db
def test_pending_cancel_is_immediate():
    from apps.conversions.services import request_cancel

    user = get_user_model().objects.create_user(username="cancel-now", password="pw")
    workspace = ensure_personal_workspace(user)
    from apps.conversions.models import ConversionJob
    from apps.files.models import FileBlob

    blob = FileBlob.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        kind=FileBlob.Kind.INPUT,
        original_name="in.csv",
    )
    blob.file.save("in.csv", SimpleUploadedFile("in.csv", b"a\n1\n"), save=True)
    job = ConversionJob.objects.create(
        owner=user,
        organization=workspace.organization,
        workspace=workspace,
        source_format="csv",
        target_format="json",
        status=ConversionJob.Status.PENDING,
        converter_name="pandas-table",
        converter_version="1.0.0",
        input_file=blob,
        original_display_filename="in.csv",
        idempotency_key="cancel-now",
    )
    request_cancel(job, actor=user)
    job.refresh_from_db()
    assert job.status == ConversionJob.Status.CANCELLED


@pytest.mark.django_db
def test_download_rejects_expired_output(client):
    from apps.conversions.models import ConversionJob
    from apps.files.models import FileBlob

    user = get_user_model().objects.create_user(username="exp-dl", password="pw")
    workspace = ensure_personal_workspace(user)
    blob = FileBlob.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        kind=FileBlob.Kind.INPUT,
        original_name="in.csv",
    )
    blob.file.save("in.csv", SimpleUploadedFile("in.csv", b"a\n1\n"), save=True)
    out = FileBlob.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        kind=FileBlob.Kind.OUTPUT,
        original_name="out.json",
    )
    out.file.save("out.json", SimpleUploadedFile("out.json", b"{}"), save=True)
    job = ConversionJob.objects.create(
        owner=user,
        organization=workspace.organization,
        workspace=workspace,
        source_format="csv",
        target_format="json",
        status=ConversionJob.Status.DONE,
        converter_name="pandas-table",
        converter_version="1.0.0",
        input_file=blob,
        output_file=out,
        original_display_filename="in.csv",
        idempotency_key="exp-dl",
        expires_at=timezone.now() - timezone.timedelta(hours=1),
    )
    client.force_login(user)
    assert client.get(f"/jobs/{job.public_id}/download/").status_code == 404
