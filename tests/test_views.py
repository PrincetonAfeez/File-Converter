# "Tests for conversion HTTP views."
"""Tests for conversion HTTP views."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.conversions.models import ConversionJob
from apps.files.models import FileBlob
from apps.organizations.models import Membership, Organization, Workspace


@pytest.mark.django_db
def test_dashboard_requires_login(client):
    assert client.get(reverse("dashboard")).status_code == 302


@pytest.mark.django_db
def test_dashboard_upload_queues_job(client, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    user = get_user_model().objects.create_user(username="dash-u", password="pw")
    from apps.organizations.services import ensure_personal_workspace

    ensure_personal_workspace(user)
    client.force_login(user)
    response = client.post(
        reverse("dashboard"),
        {
            "file": SimpleUploadedFile("d.csv", b"a,b\n1,2\n", content_type="text/csv"),
            "target_format": "json",
            "idempotency_key": "dash-upload-1",
        },
    )
    assert response.status_code == 302
    assert ConversionJob.objects.filter(idempotency_key="dash-upload-1").exists()


@pytest.mark.django_db
def test_job_detail_and_status_partial(client, make_job):
    job, user, _ws = make_job("view-user")
    client.force_login(user)
    detail = client.get(reverse("job_detail", args=[job.public_id]))
    assert detail.status_code == 200
    status = client.get(reverse("job_status", args=[job.public_id]))
    assert status.status_code == 200
    assert b"job-status" in status.content


@pytest.mark.django_db
def test_job_list_pagination(client, make_job):
    job, user, _ws = make_job("list-user")
    client.force_login(user)
    response = client.get(reverse("job_list"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_cancel_terminal_job_shows_info(client, make_job):
    job, user, _ws = make_job("cancel-term", status=ConversionJob.Status.DONE)
    client.force_login(user)
    response = client.post(reverse("cancel_job", args=[job.public_id]))
    assert response.status_code == 302
    follow = client.get(reverse("job_detail", args=[job.public_id]))
    assert follow.status_code == 200


@pytest.mark.django_db
def test_download_owner_success(client, make_job):
    job, user, ws = make_job("dl-user", status=ConversionJob.Status.DONE)
    out = FileBlob.objects.create(
        organization=ws.organization,
        workspace=ws,
        kind=FileBlob.Kind.OUTPUT,
        original_name="out.json",
    )
    out.file.save("out.json", SimpleUploadedFile("out.json", b"{}"), save=True)
    job.output_file = out
    job.expires_at = timezone.now() + timezone.timedelta(hours=1)
    job.save(update_fields=["output_file", "expires_at"])
    client.force_login(user)
    response = client.get(reverse("download_job", args=[job.public_id]))
    assert response.status_code == 200


@pytest.mark.django_db
def test_workspace_switch_post(client, make_user):
    user = make_user("switcher")
    org = Organization.objects.create(name="Switch Org")
    ws1 = Workspace.objects.create(organization=org, name="A")
    ws2 = Workspace.objects.create(organization=org, name="B")
    Membership.objects.create(user=user, organization=org, role=Membership.Role.OWNER)
    client.force_login(user)
    response = client.post(
        reverse("dashboard"),
        {"action": "switch_workspace", "workspace_id": str(ws2.public_id)},
    )
    assert response.status_code == 302
