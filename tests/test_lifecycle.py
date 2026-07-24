# "Tests for retention, quotas, and lifecycle."
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.audit.models import OutboxEvent
from apps.conversions.models import ConversionJob, JobEvent
from apps.conversions.services import (
    expire_due_outputs,
    garbage_collect_blobs,
    purge_terminal_input_files,
    requeue_stale_jobs,
    submit_conversion_job,
    terminal_transition,
)
from apps.files.models import FileBlob
from apps.files.utils import validate_content_type
from apps.organizations.models import (
    Membership,
    Organization,
    Workspace,
    WorkspaceMembership,
)
from apps.organizations.services import ensure_personal_workspace, user_can_access_workspace
from apps.quotas.models import QuotaDecision
from apps.quotas.services import purge_expired_quota_decisions


def _make_job(username, *, status=ConversionJob.Status.PROCESSING, claim_generation=1):
    user = get_user_model().objects.create_user(username=username, password="pw")
    workspace = ensure_personal_workspace(user)
    blob = FileBlob.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        kind=FileBlob.Kind.INPUT,
        original_name="input.csv",
        byte_size=4,
    )
    blob.file.save("input.csv", SimpleUploadedFile("input.csv", b"a\n1\n"), save=True)
    job = ConversionJob.objects.create(
        owner=user,
        organization=workspace.organization,
        workspace=workspace,
        source_format="csv",
        target_format="json",
        status=status,
        claim_generation=claim_generation,
        converter_name="pandas-table",
        converter_version="1.0.0",
        input_file=blob,
        original_display_filename="input.csv",
        idempotency_key=username,
        input_byte_size=4,
    )
    return job


# --- Fix #3: only DONE jobs are billed -------------------------------------------------
@pytest.mark.django_db
def test_failed_and_cancelled_jobs_are_not_billed():
    from apps.quotas.models import UsageLedger

    failed = _make_job("bill-fail")
    assert terminal_transition(failed, 1, ConversionJob.Status.FAILED, message="boom") is True

    cancelled = _make_job("bill-cancel")
    assert terminal_transition(cancelled, 1, ConversionJob.Status.CANCELLED, message="stop") is True

    assert UsageLedger.objects.count() == 0

    done = _make_job("bill-done")
    assert terminal_transition(done, 1, ConversionJob.Status.DONE, progress_percent=100) is True
    assert UsageLedger.objects.filter(reason="conversion.done").count() == 1


# --- Fix #6: expired outputs are purged and marked EXPIRED ------------------------------
@pytest.mark.django_db
def test_expire_due_outputs_purges_and_marks_expired():
    job = _make_job("expire-me", status=ConversionJob.Status.DONE)
    output = FileBlob.objects.create(
        organization=job.organization,
        workspace=job.workspace,
        kind=FileBlob.Kind.OUTPUT,
        original_name="out.json",
        byte_size=2,
    )
    output.file.save("out.json", SimpleUploadedFile("out.json", b"{}"), save=True)
    job.output_file = output
    job.expires_at = timezone.now() - timezone.timedelta(hours=1)
    job.save(update_fields=["output_file", "expires_at"])

    assert expire_due_outputs() == 1

    job.refresh_from_db()
    output.refresh_from_db()
    assert job.status == ConversionJob.Status.EXPIRED
    assert job.output_file is None
    assert output.deleted_at is not None
    assert not output.file.storage.exists(output.file.name)


# --- Fix #4: stale PROCESSING jobs are requeued ----------------------------------------
@pytest.mark.django_db
def test_requeue_stale_jobs(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    job = _make_job("stale-me", status=ConversionJob.Status.PROCESSING)
    job.heartbeat_at = timezone.now() - timezone.timedelta(minutes=30)
    job.save(update_fields=["heartbeat_at"])

    assert requeue_stale_jobs() == 1
    assert JobEvent.objects.filter(job=job, event_type="job.requeued_stale").exists()

    job.refresh_from_db()
    # It was flipped out of the stuck PROCESSING+old-heartbeat state (and, eagerly, reprocessed).
    assert job.status != ConversionJob.Status.PROCESSING


# --- Fix #7: content sniffing rejects clear extension mismatches ------------------------
def test_validate_content_type():
    # Clear contradiction is rejected.
    with pytest.raises(ValueError):
        validate_content_type("png", "text/plain")
    # Matching / tolerated cases pass silently.
    validate_content_type("png", "image/png")
    validate_content_type("csv", "text/plain")
    validate_content_type("xlsx", "application/zip")
    validate_content_type("csv", "")  # detection unavailable -> skip
    validate_content_type("png", "application/octet-stream")  # ambiguous -> allowed


# --- Fix #2: organization slugs never collide ------------------------------------------
@pytest.mark.django_db
def test_duplicate_org_names_get_unique_slugs():
    a = Organization.objects.create(name="Acme Inc")
    b = Organization.objects.create(name="Acme Inc")
    c = Organization.objects.create(name="Acme Inc")
    slugs = {a.slug, b.slug, c.slug}
    assert len(slugs) == 3
    assert a.slug == "acme-inc"


# --- Round 2 #1: duplicate idempotent submits never leak blobs/quota rows --------------
@pytest.mark.django_db
def test_duplicate_idempotent_submit_does_not_leak(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    user = get_user_model().objects.create_user(username="idem", password="pw")
    workspace = ensure_personal_workspace(user)

    def submit():
        upload = SimpleUploadedFile("d.csv", b"a,b\n1,2\n", content_type="text/csv")
        return submit_conversion_job(
            user=user,
            workspace=workspace,
            uploaded_file=upload,
            target_format="json",
            idempotency_key="SAME-KEY",
            options={},
        )

    first = submit()
    second = submit()
    third = submit()

    assert first.pk == second.pk == third.pk
    assert ConversionJob.objects.count() == 1
    assert FileBlob.objects.filter(kind=FileBlob.Kind.INPUT).count() == 1
    assert QuotaDecision.objects.count() == 1
    orphans = FileBlob.objects.filter(kind=FileBlob.Kind.INPUT).exclude(
        pk__in=ConversionJob.objects.values("input_file")
    )
    assert orphans.count() == 0


# --- Round 2 #2 / Round 3 #1+#4: progress persists to DB, refreshes heartbeat, is fenced -
@pytest.mark.django_db
def test_progress_reporter_persists_and_refreshes_heartbeat():
    from apps.conversions.progress import ProgressReporter

    job = _make_job("progress")  # PROCESSING, claim_generation=1
    reporter = ProgressReporter(job.public_id, job_pk=job.pk, claim_generation=1)
    reporter(42, "Halfway")

    job.refresh_from_db()
    assert job.progress_percent == 42
    assert job.progress_message == "Halfway"
    assert job.heartbeat_at is not None  # heartbeat refreshed so a live job is not reaped


@pytest.mark.django_db
def test_progress_reporter_is_fenced_by_claim_generation():
    from apps.conversions.progress import ProgressReporter

    job = _make_job("fenced", claim_generation=5)
    # A superseded worker (older generation) must not mutate the job it no longer owns.
    stale_reporter = ProgressReporter(job.public_id, job_pk=job.pk, claim_generation=1)
    stale_reporter(77, "stale")

    job.refresh_from_db()
    assert job.progress_percent == 0
    assert job.progress_message != "stale"


# --- Round 3 #3: spreadsheet formula injection is neutralized on CSV/XLSX output --------
def test_data_converter_escapes_formula_cells(tmp_path):
    import pandas as pd

    from apps.converters.data import DataTableConverter

    src = tmp_path / "in.csv"
    src.write_text("name,note\n=1+1,+cmd\nsafe,@ref\n", encoding="utf-8")
    out = tmp_path / "out.csv"
    DataTableConverter().convert(src, out, "csv", {})

    frame = pd.read_csv(out, dtype=str)
    values = frame.values.ravel().tolist()
    assert "=1+1" not in values and "'=1+1" in values
    assert "+cmd" not in values and "'+cmd" in values
    assert "'@ref" in values


# --- Round 3 #5: oversized tabular inputs are rejected ----------------------------------
def test_data_converter_rejects_oversized_table(tmp_path, settings):
    settings.FILECONVERTER_MAX_TABLE_ROWS = 3
    from apps.converters.data import DataTableConverter

    src = tmp_path / "big.csv"
    src.write_text("n\n" + "\n".join(str(i) for i in range(10)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        DataTableConverter().convert(src, tmp_path / "out.json", "json", {})


# --- Round 3 #2: active-jobs ceiling is enforced ---------------------------------------
@pytest.mark.django_db
def test_active_jobs_quota_enforced(settings):
    from apps.quotas.models import UsageQuota

    user = get_user_model().objects.create_user(username="quota", password="pw")
    workspace = ensure_personal_workspace(user)
    UsageQuota.objects.create(
        organization=workspace.organization, workspace=workspace, max_active_jobs=2
    )

    def submit(key):
        upload = SimpleUploadedFile("d.csv", b"a,b\n1,2\n", content_type="text/csv")
        return submit_conversion_job(
            user=user,
            workspace=workspace,
            uploaded_file=upload,
            target_format="json",
            idempotency_key=key,
            options={},
        )

    submit("a")
    submit("b")
    with pytest.raises(ValueError, match="Quota denied"):
        submit("c")
    # The denied attempt left no input blob behind and recorded a denial for audit.
    assert ConversionJob.objects.filter(workspace=workspace).count() == 2
    assert FileBlob.objects.filter(kind=FileBlob.Kind.INPUT).count() == 2
    assert QuotaDecision.objects.filter(result=QuotaDecision.Result.DENIED).count() == 1


# --- Round 3 #9: stale jobs past the attempt budget are failed, not requeued forever ----
@pytest.mark.django_db
def test_stale_job_over_attempt_budget_is_failed(settings):
    settings.FILECONVERTER_MAX_ATTEMPTS = 3
    job = _make_job("poison", status=ConversionJob.Status.PROCESSING)
    ConversionJob.objects.filter(pk=job.pk).update(
        attempt_count=3, heartbeat_at=timezone.now() - timezone.timedelta(minutes=30)
    )

    assert requeue_stale_jobs() == 0  # nothing requeued
    job.refresh_from_db()
    assert job.status == ConversionJob.Status.FAILED


# --- Round 4 #1+#7: reaper failure emits the outbox event and clears the progress bar ---
@pytest.mark.django_db
def test_reaper_failure_emits_outbox_and_sets_progress(settings):
    settings.FILECONVERTER_MAX_ATTEMPTS = 3
    job = _make_job("reaperfail", status=ConversionJob.Status.PROCESSING)
    ConversionJob.objects.filter(pk=job.pk).update(
        attempt_count=3,
        progress_percent=50,
        heartbeat_at=timezone.now() - timezone.timedelta(minutes=30),
    )

    requeue_stale_jobs()
    job.refresh_from_db()
    assert job.status == ConversionJob.Status.FAILED
    assert job.progress_percent == 0
    assert OutboxEvent.objects.filter(
        idempotency_key=f"job:{job.public_id}:failed", event_type="conversion.job.failed"
    ).exists()


# --- Round 4 #2: terminal jobs past retention have their input bytes reclaimed -----------
@pytest.mark.django_db
def test_purge_terminal_input_files(settings):
    settings.FILECONVERTER_INPUT_TTL_HOURS = 24
    job = _make_job("purgein", status=ConversionJob.Status.DONE)
    old = timezone.now() - timezone.timedelta(hours=48)
    ConversionJob.objects.filter(pk=job.pk).update(created_at=old, finished_at=old)
    blob = job.input_file
    assert blob.file.storage.exists(blob.file.name)

    assert purge_terminal_input_files() == 1
    blob.refresh_from_db()
    assert blob.deleted_at is not None
    assert not blob.file.storage.exists(blob.file.name)
    # Row is retained (input_file FK is PROTECT); the job is untouched otherwise.
    job.refresh_from_db()
    assert job.input_file_id == blob.pk


# --- Round 4 #4: workspace-level ACL is enforced when WorkspaceMembership is configured --
@pytest.mark.django_db
def test_workspace_membership_enforced_in_shared_org():
    org = Organization.objects.create(name="Shared Co")
    ws = Workspace.objects.create(organization=org, name="Team")
    owner = get_user_model().objects.create_user(username="owner", password="pw")
    member = get_user_model().objects.create_user(username="member", password="pw")
    Membership.objects.create(user=owner, organization=org, role=Membership.Role.OWNER)
    Membership.objects.create(user=member, organization=org, role=Membership.Role.MEMBER)

    # No workspace ACL yet -> org membership suffices for everyone.
    assert user_can_access_workspace(member, ws) is True

    # Once explicit workspace membership exists, a non-listed MEMBER is denied; OWNER still ok.
    WorkspaceMembership.objects.create(user=owner, workspace=ws)
    assert user_can_access_workspace(member, ws) is False
    assert user_can_access_workspace(owner, ws) is True
    WorkspaceMembership.objects.create(user=member, workspace=ws)
    assert user_can_access_workspace(member, ws) is True


# --- Round 5 #1: client IP honors X-Forwarded-For behind trusted proxies ----------------
def test_client_ip_from_request(settings):
    from apps.files.utils import client_ip_from_request

    class Req:
        def __init__(self, meta):
            self.META = meta

    settings.FILECONVERTER_TRUSTED_PROXY_COUNT = 0
    r = Req({"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "1.2.3.4, 10.0.0.1"})
    assert client_ip_from_request(r) == "10.0.0.1"  # no proxy trusted -> REMOTE_ADDR

    settings.FILECONVERTER_TRUSTED_PROXY_COUNT = 1
    assert client_ip_from_request(r) == "1.2.3.4"  # one hop from the right = real client

    # Missing header falls back to REMOTE_ADDR even when proxies are configured.
    assert client_ip_from_request(Req({"REMOTE_ADDR": "10.0.0.1"})) == "10.0.0.1"


# --- Round 5 #2: unreferenced blobs are garbage-collected -------------------------------
@pytest.mark.django_db
def test_garbage_collect_orphan_blobs(settings):
    settings.FILECONVERTER_BLOB_GC_TTL_HOURS = 48
    user = get_user_model().objects.create_user(username="gc", password="pw")
    workspace = ensure_personal_workspace(user)
    orphan = FileBlob.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        kind=FileBlob.Kind.OUTPUT,
        original_name="orphan.json",
        byte_size=2,
    )
    orphan.file.save("orphan.json", SimpleUploadedFile("orphan.json", b"{}"), save=True)
    name = orphan.file.name
    FileBlob.objects.filter(pk=orphan.pk).update(
        created_at=timezone.now() - timezone.timedelta(hours=72)
    )
    # A referenced input blob (has a job) must NOT be collected.
    referenced = _make_job("gcref").input_file
    FileBlob.objects.filter(pk=referenced.pk).update(
        created_at=timezone.now() - timezone.timedelta(hours=72)
    )

    assert garbage_collect_blobs() == 1
    assert not FileBlob.objects.filter(pk=orphan.pk).exists()
    assert not orphan.file.storage.exists(name)
    assert FileBlob.objects.filter(pk=referenced.pk).exists()


# --- Round 5 #7: input blob is persisted with a checksum in a single write ---------------
@pytest.mark.django_db
def test_input_blob_has_checksum(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    user = get_user_model().objects.create_user(username="chk", password="pw")
    workspace = ensure_personal_workspace(user)
    job = submit_conversion_job(
        user=user,
        workspace=workspace,
        uploaded_file=SimpleUploadedFile("d.csv", b"a,b\n1,2\n", content_type="text/csv"),
        target_format="json",
        idempotency_key="chk1",
        options={},
    )
    assert job.input_file.sha256 != ""
    assert len(job.input_file.sha256) == 64


# --- Round 5 #3: webhook payloads are HMAC-signed when a secret is configured ------------
@pytest.mark.django_db
def test_webhook_signature(settings, monkeypatch):
    import hashlib
    import hmac

    from apps.audit import services as audit_services
    from apps.audit.models import OutboxEvent as OE

    org = Organization.objects.create(name="Signed Co")
    audit_services.enqueue_outbox(
        organization=org, event_type="conversion.job.done", idempotency_key="sig1", payload={"a": 1}
    )
    settings.FILECONVERTER_WEBHOOK_URL = "https://hooks.example/ingest"
    settings.FILECONVERTER_WEBHOOK_SECRET = "topsecret"

    captured = {}

    def fake_open(request, timeout=None):
        captured["sig"] = request.headers.get("X-signature")
        captured["body"] = request.data

        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(audit_services._OPENER, "open", fake_open)
    assert audit_services.deliver_outbox_events() == 1
    expected = "sha256=" + hmac.new(b"topsecret", captured["body"], hashlib.sha256).hexdigest()
    assert captured["sig"] == expected
    assert OE.objects.get(idempotency_key="sig1").delivered_at is not None


# --- Round 5 #4: non-HTTP webhook schemes are refused ------------------------------------
def test_webhook_rejects_non_http_scheme(settings):
    from apps.audit import services as audit_services
    from apps.audit.models import OutboxEvent as OE

    event = OE(event_type="x", idempotency_key="k", payload={})
    with pytest.raises(ValueError):
        audit_services._deliver_one(event, "file:///etc/passwd", 5.0)


# --- Round 4 #9: outbox delivery marks events delivered and purges old ones --------------
@pytest.mark.django_db
def test_outbox_delivery_and_purge(settings, monkeypatch):
    from apps.audit import services as audit_services

    org = Organization.objects.create(name="Hooky")
    event = audit_services.enqueue_outbox(
        organization=org, event_type="conversion.job.done", idempotency_key="k1", payload={"x": 1}
    )

    # No webhook configured -> no-op, event stays pending.
    settings.FILECONVERTER_WEBHOOK_URL = ""
    assert audit_services.deliver_outbox_events() == 0
    event.refresh_from_db()
    assert event.delivered_at is None

    # With a webhook configured, delivery is attempted; stub the HTTP call as success.
    settings.FILECONVERTER_WEBHOOK_URL = "https://hooks.example/ingest"
    monkeypatch.setattr(audit_services, "_deliver_one", lambda *a, **k: None)
    assert audit_services.deliver_outbox_events() == 1
    event.refresh_from_db()
    assert event.delivered_at is not None

    # Purge removes delivered rows past the TTL.
    OutboxEvent.objects.filter(pk=event.pk).update(
        delivered_at=timezone.now() - timezone.timedelta(days=30)
    )
    assert audit_services.purge_delivered_outbox_events(older_than_days=7) == 1
    assert not OutboxEvent.objects.filter(pk=event.pk).exists()


# --- Round 2 #8: quota decisions past retention are purged ------------------------------
@pytest.mark.django_db
def test_purge_expired_quota_decisions():
    user = get_user_model().objects.create_user(username="purge", password="pw")
    workspace = ensure_personal_workspace(user)
    old = QuotaDecision.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        user=user,
        result=QuotaDecision.Result.ALLOWED,
        requested_bytes=1,
    )
    QuotaDecision.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=40)
    )
    fresh = QuotaDecision.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        user=user,
        result=QuotaDecision.Result.ALLOWED,
        requested_bytes=1,
    )

    assert purge_expired_quota_decisions(older_than_days=30) == 1
    assert QuotaDecision.objects.filter(pk=fresh.pk).exists()
    assert not QuotaDecision.objects.filter(pk=old.pk).exists()
