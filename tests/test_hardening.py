# "Security and multi-tenant hardening tests."
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.audit.models import OutboxEvent
from apps.conversions.models import ConversionJob, JobEvent
from apps.conversions.services import requeue_stale_jobs
from apps.files.models import FileBlob
from apps.organizations.lifecycle import delete_user_account, export_user_data
from apps.organizations.models import Organization
from apps.organizations.services import ensure_personal_workspace


def _mk_job(username, *, status=ConversionJob.Status.PROCESSING):
    user = get_user_model().objects.create_user(username=username, password="pw")
    ws = ensure_personal_workspace(user)
    blob = FileBlob.objects.create(
        organization=ws.organization,
        workspace=ws,
        kind=FileBlob.Kind.INPUT,
        original_name="in.csv",
        byte_size=4,
    )
    blob.file.save("in.csv", SimpleUploadedFile("in.csv", b"a\n1\n"), save=True)
    job = ConversionJob.objects.create(
        owner=user,
        organization=ws.organization,
        workspace=ws,
        source_format="csv",
        target_format="json",
        status=status,
        claim_generation=1,
        converter_name="pandas-table",
        converter_version="1.0.0",
        input_file=blob,
        original_display_filename="in.csv",
        idempotency_key=username,
        input_byte_size=4,
    )
    return job


# --- #14: cross-tenant isolation is enforced structurally --------------------------------
@pytest.mark.django_db
def test_cross_tenant_job_detail_is_not_found(client):
    job = _mk_job("tenant_a")
    other = get_user_model().objects.create_user(username="tenant_b", password="pw")
    ensure_personal_workspace(other)  # other user gets their own org
    url = f"/jobs/{job.public_id}/"

    client.force_login(other)
    assert client.get(url).status_code == 404  # not in other's tenant scope
    client.force_login(job.owner)
    assert client.get(url).status_code == 200  # owner can see it


@pytest.mark.django_db
def test_cross_tenant_download_is_not_found(client):
    job = _mk_job("dl_a")
    other = get_user_model().objects.create_user(username="dl_b", password="pw")
    ensure_personal_workspace(other)
    client.force_login(other)
    assert client.get(f"/jobs/{job.public_id}/download/").status_code == 404


# --- #8/#15: account deletion purges all tenant data ------------------------------------
@pytest.mark.django_db
def test_delete_user_account_purges_data():
    job = _mk_job("delme", status=ConversionJob.Status.DONE)
    blob = job.input_file
    name = blob.file.name
    org_id = job.organization_id

    delete_user_account(job.owner)

    assert not ConversionJob.objects.filter(pk=job.pk).exists()
    assert not FileBlob.objects.filter(pk=blob.pk).exists()
    assert not blob.file.storage.exists(name)
    assert not Organization.objects.filter(pk=org_id).exists()


@pytest.mark.django_db
def test_export_user_data_contains_jobs():
    job = _mk_job("exporter")
    data = export_user_data(job.owner)
    assert data["user"]["username"] == "exporter"
    assert len(data["conversion_jobs"]) == 1
    assert data["conversion_jobs"][0]["public_id"] == str(job.public_id)
    assert data["organizations"]


# --- #16: exhausted stale jobs emit a dead-letter signal --------------------------------
@pytest.mark.django_db
def test_reaper_emits_dead_letter(settings):
    settings.FILECONVERTER_MAX_ATTEMPTS = 3
    job = _mk_job("deadletter")
    ConversionJob.objects.filter(pk=job.pk).update(
        attempt_count=3, heartbeat_at=timezone.now() - timezone.timedelta(minutes=30)
    )

    requeue_stale_jobs()

    assert JobEvent.objects.filter(job=job, event_type="job.dead_letter").exists()
    assert OutboxEvent.objects.filter(
        idempotency_key=f"job:{job.public_id}:dead_letter",
        event_type="conversion.job.dead_letter",
    ).exists()


# --- #7: upload rate limit trips after the configured maximum ---------------------------
def test_upload_rate_limit(settings):
    from apps.conversions.services import _enforce_upload_rate

    cache.clear()
    settings.FILECONVERTER_UPLOAD_RATE_MAX = 2

    class _User:
        pk = 987654321

    user = _User()
    _enforce_upload_rate(user)
    _enforce_upload_rate(user)
    with pytest.raises(ValueError, match="too quickly"):
        _enforce_upload_rate(user)


# --- #3/#20: security headers are present -----------------------------------------------
@pytest.mark.django_db
def test_security_headers_present(client):
    response = client.get("/accounts/login/")
    assert "Content-Security-Policy" in response
    assert response.headers.get("X-Request-ID")


# --- AUTH6: workspace-ACL denies an org member not on the workspace (privilege boundary) -
@pytest.mark.django_db
def test_member_without_workspace_membership_is_denied(client):
    from apps.organizations.models import Membership, Organization, Workspace, WorkspaceMembership

    org = Organization.objects.create(name="Shared Priv Co")
    ws = Workspace.objects.create(organization=org, name="Team")
    owner = get_user_model().objects.create_user("priv_owner", password="pw")
    member = get_user_model().objects.create_user("priv_member", password="pw")
    Membership.objects.create(user=owner, organization=org, role=Membership.Role.OWNER)
    Membership.objects.create(user=member, organization=org, role=Membership.Role.MEMBER)
    WorkspaceMembership.objects.create(user=owner, workspace=ws)  # ACL now active, owner only

    blob = FileBlob.objects.create(
        organization=org, workspace=ws, kind=FileBlob.Kind.INPUT, original_name="in.csv"
    )
    blob.file.save("in.csv", SimpleUploadedFile("in.csv", b"a\n1\n"), save=True)
    job = ConversionJob.objects.create(
        owner=owner, organization=org, workspace=ws, source_format="csv", target_format="json",
        converter_name="pandas-table", converter_version="1.0.0", input_file=blob,
        original_display_filename="in.csv", idempotency_key="priv1",
    )
    url = f"/jobs/{job.public_id}/"
    # Member is in the org (passes tenant scope) but not on the workspace ACL -> 403.
    client.force_login(member)
    assert client.get(url).status_code == 403
    client.force_login(owner)
    assert client.get(url).status_code == 200


# --- G6: feature flag resolution order (settings override > DB row > default) ------------
@pytest.mark.django_db
def test_feature_flag_resolution(settings):
    from apps.ops.flags import flag_enabled
    from apps.ops.models import FeatureFlag

    cache.clear()
    assert flag_enabled("beta_x", default=False) is False  # unset -> default
    FeatureFlag.objects.create(name="beta_x", enabled=True)
    cache.clear()
    assert flag_enabled("beta_x", default=False) is True  # DB row wins over default
    settings.FILECONVERTER_FLAGS = {"beta_x": False}
    assert flag_enabled("beta_x", default=False) is False  # settings override wins


# --- G4/BG6 + OPS_metrics_endpoint_auth: metrics gauges, gated behind auth ---------------
@pytest.mark.django_db
def test_metrics_requires_auth(client, settings):
    settings.FILECONVERTER_METRICS_TOKEN = ""
    # Anonymous is always denied.
    assert client.get("/ops/metrics/").status_code == 403


@pytest.mark.django_db
def test_metrics_with_token_and_staff(client, settings, monkeypatch):
    from apps.ops import metrics as metrics_mod

    monkeypatch.setattr(metrics_mod, "queue_depth", lambda queue="default": 7)
    _mk_job("metricsjob")
    settings.FILECONVERTER_METRICS_TOKEN = "scrape-me"

    # Wrong/no token -> denied.
    assert client.get("/ops/metrics/").status_code == 403
    # Correct bearer token -> allowed.
    ok = client.get("/ops/metrics/", HTTP_AUTHORIZATION="Bearer scrape-me")
    assert ok.status_code == 200
    data = ok.json()
    assert data["queue_depth"]["default"] == 7
    assert "job_status_counts" in data
    assert "dead_letter_total" in data
    assert "outbox_failed_total" in data
    assert "outbox_pending_total" in data

    # Staff session -> allowed even without a token header.
    staff = get_user_model().objects.create_user("metrics_staff", password="pw", is_staff=True)
    client.force_login(staff)
    settings.FILECONVERTER_METRICS_TOKEN = ""
    assert client.get("/ops/metrics/").status_code == 200
