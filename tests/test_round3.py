# "Regression tests from round-three audit fixes."
"""Round 3 fix regression tests."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.conversions.models import ConversionJob
from apps.conversions.services import submit_conversion_job
from apps.files.models import FileBlob
from apps.organizations.models import Membership, Organization, Workspace
from apps.organizations.services import ensure_personal_workspace


@pytest.mark.django_db
def test_readiness_degraded_returns_503(client, settings, monkeypatch):
    from apps.ops import views as ops_views

    monkeypatch.setattr(ops_views, "_check_rls", lambda: "degraded")
    response = client.get("/ops/ready/")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


@pytest.mark.django_db
def test_idempotency_rejects_finished_job_resubmit(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    user = get_user_model().objects.create_user(username="idem-dead", password="pw")
    workspace = ensure_personal_workspace(user)
    upload = SimpleUploadedFile("d.csv", b"a,b\n1,2\n", content_type="text/csv")
    submit_conversion_job(
        user=user,
        workspace=workspace,
        uploaded_file=upload,
        target_format="json",
        idempotency_key="dead-key",
        options={},
    )
    ConversionJob.objects.filter(idempotency_key="dead-key").update(
        status=ConversionJob.Status.FAILED
    )
    with pytest.raises(ValueError, match="finished conversion"):
        submit_conversion_job(
            user=user,
            workspace=workspace,
            uploaded_file=upload,
            target_format="json",
            idempotency_key="dead-key",
            options={},
        )


@pytest.mark.django_db
def test_member_cannot_download_other_users_job(client):
    owner = get_user_model().objects.create_user(username="owner-dl", password="pw")
    other = get_user_model().objects.create_user(username="other-dl", password="pw")
    org = Organization.objects.create(name="Shared Org")
    ws = Workspace.objects.create(organization=org, name="General")
    Membership.objects.create(user=owner, organization=org, role=Membership.Role.MEMBER)
    Membership.objects.create(user=other, organization=org, role=Membership.Role.MEMBER)

    blob = FileBlob.objects.create(
        organization=org, workspace=ws, kind=FileBlob.Kind.INPUT, original_name="in.csv"
    )
    blob.file.save("in.csv", SimpleUploadedFile("in.csv", b"a,b\n1,2\n"), save=True)
    out = FileBlob.objects.create(
        organization=org, workspace=ws, kind=FileBlob.Kind.OUTPUT, original_name="out.json"
    )
    out.file.save("out.json", SimpleUploadedFile("out.json", b"{}"), save=True)
    job = ConversionJob.objects.create(
        owner=owner,
        organization=org,
        workspace=ws,
        source_format="csv",
        target_format="json",
        status=ConversionJob.Status.DONE,
        converter_name="pandas-table",
        converter_version="1.0.0",
        input_file=blob,
        output_file=out,
        original_display_filename="mine.csv",
        idempotency_key="owner-only",
        expires_at=timezone.now() + timezone.timedelta(hours=1),
    )
    client.force_login(other)
    assert client.get(f"/jobs/{job.public_id}/download/").status_code == 403


@pytest.mark.django_db
def test_repeat_cancel_does_not_duplicate_events():
    from apps.conversions.services import request_cancel

    user = get_user_model().objects.create_user(username="cancel-dup", password="pw")
    workspace = ensure_personal_workspace(user)
    from apps.conversions.models import ConversionJob, JobEvent
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
        status=ConversionJob.Status.PROCESSING,
        converter_name="pandas-table",
        converter_version="1.0.0",
        input_file=blob,
        original_display_filename="in.csv",
        idempotency_key="cancel-dup",
    )
    request_cancel(job, actor=user)
    request_cancel(job, actor=user)
    assert (
        JobEvent.objects.filter(job=job, event_type="job.cancel_requested").count() == 1
    )


@pytest.mark.django_db
def test_disabled_member_session_is_logged_out(client):
    user = get_user_model().objects.create_user(username="disabled-session", password="pw")
    org = Organization.objects.create(name="Disable Co")
    Membership.objects.create(user=user, organization=org, role=Membership.Role.MEMBER)
    Workspace.objects.create(organization=org, name="General")
    assert client.login(username="disabled-session", password="pw") is True
    Membership.objects.filter(user=user).update(status=Membership.Status.DISABLED)
    response = client.get("/dashboard/")
    assert response.status_code == 302
    assert "/accounts/login/" in response.url
