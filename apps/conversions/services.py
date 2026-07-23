# "Core conversion submit, claim, and run logic."
from __future__ import annotations

import logging
import os
import platform
import subprocess
import tempfile
import traceback
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import models, transaction
from django.utils import timezone

from apps.audit.services import enqueue_outbox, write_audit
from apps.converters.interface import TransientConversionError
from apps.converters.registry import UnsupportedFormatPair, registry
from apps.files.models import FileBlob
from apps.files.utils import (
    detect_mime_from_upload,
    extension_for_path,
    mime_scanner_available,
    sanitize_filename,
    sha256_file,
    sha256_upload,
    validate_content_type,
    validate_upload_size,
)
from apps.ops.flags import flag_enabled
from apps.organizations.permissions import user_may_write_workspace
from apps.organizations.services import organization_is_active, output_ttl_hours
from apps.quotas.models import QuotaDecision, UsageLedger
from apps.quotas.services import record_quota_denial, reserve_quota
from fileconverter.ratelimit import rate_limit_commit, rate_limit_would_exceed

from .models import ConversionJob, JobEvent
from .progress import ProgressReporter

logger = logging.getLogger(__name__)

STALE_PROCESSING_AFTER = timedelta(minutes=10)

# Upper bound on rows touched per beat pass so a large backlog cannot be materialized at
# once; the periodic schedule drains any remainder on subsequent runs.
MAINTENANCE_BATCH_SIZE = 200


class JobCancelled(Exception):
    pass


class JobFenced(Exception):
    """The job's claim was superseded by a newer worker; abort this attempt quietly."""


# Re-export for callers that imported from this module historically.
TRANSIENT_EXCEPTIONS = (
    TransientConversionError,
    TimeoutError,
    subprocess.TimeoutExpired,
    ConnectionError,
    BrokenPipeError,
)


def _enforce_upload_rate(user) -> None:
    key = f"upload-rate:{user.pk}"
    if rate_limit_would_exceed(key, limit=settings.FILECONVERTER_UPLOAD_RATE_MAX):
        raise ValueError("You're submitting too quickly. Please wait a moment.")
    rate_limit_commit(key, window_seconds=settings.FILECONVERTER_UPLOAD_RATE_WINDOW_SECONDS)


def _idempotent_job_returnable(job: ConversionJob) -> bool:
    if job.status in {
        ConversionJob.Status.PENDING,
        ConversionJob.Status.PROCESSING,
        ConversionJob.Status.RETRYING,
    }:
        return True
    if job.status == ConversionJob.Status.DONE and job.output_file_id:
        if job.output_file.deleted_at is not None:
            return False
        return not (job.expires_at and job.expires_at <= timezone.now())
    return False


def submit_conversion_job(
    *, user, workspace, uploaded_file, target_format: str, idempotency_key: str, options=None
):
    if not organization_is_active(workspace.organization):
        raise ValueError("This organization is suspended and cannot accept new conversions.")

    if not flag_enabled("upload_enabled", default=True):
        raise ValueError("Uploads are temporarily disabled.")

    if not user_may_write_workspace(user, workspace):
        raise ValueError("You do not have permission to upload to this workspace.")

    validate_upload_size(uploaded_file)
    display_name = sanitize_filename(uploaded_file.name)
    source_format = extension_for_path(display_name)
    target_format = target_format.lower().strip()
    converter = registry.get(source_format, target_format)
    if converter.converter_name == "libreoffice-pdf" and not flag_enabled(
        "document_conversion", default=True
    ):
        raise ValueError("Document conversion is temporarily disabled.")
    if converter.converter_name == "ffmpeg-media" and not flag_enabled(
        "media_conversion", default=True
    ):
        raise ValueError("Media conversion is temporarily disabled.")
    cleaned_options = converter.option_schema().validate(options or {})

    detected_mime = detect_mime_from_upload(uploaded_file)
    if settings.FILECONVERTER_ENFORCE_MIME_MATCH:
        if not mime_scanner_available():
            # The control is enabled but the scanner is missing. Fail closed if the
            # deployment demands it; otherwise degrade loudly rather than silently.
            if settings.FILECONVERTER_REQUIRE_MIME_SCANNER:
                raise ValueError(
                    "Upload content scanning is required but unavailable on this server."
                )
            logger.warning(
                "MIME scanner unavailable; content check skipped for %s", display_name
            )
        validate_content_type(source_format, detected_mime)

    upload_checksum = sha256_upload(uploaded_file)

    # Idempotency fast-path: a duplicate submission must not reserve quota or persist a
    # second input blob. The DB unique constraint is the source of truth; this check just
    # avoids the common browser-retry / double-click leak before doing any work.
    existing = ConversionJob.objects.filter(
        owner=user, workspace=workspace, idempotency_key=idempotency_key
    ).select_related("output_file").first()
    if existing:
        _verify_idempotent_match(
            existing,
            source_format=source_format,
            target_format=target_format,
            upload_checksum=upload_checksum,
            options=cleaned_options,
        )
        if _idempotent_job_returnable(existing):
            return existing
        raise ValueError(
            "This idempotency key was already used for a finished conversion. "
            "Use a new idempotency key to submit again."
        )

    if workspace.allow_output_reuse:
        reused = _find_reusable_job(
            user=user,
            workspace=workspace,
            source_format=source_format,
            target_format=target_format,
            upload_checksum=upload_checksum,
            options=cleaned_options,
        )
        if reused is not None:
            write_audit(
                organization=workspace.organization,
                workspace=workspace,
                actor=user,
                event_type="conversion.output_reused",
                obj=reused,
                message=f"Served cached output for {display_name}",
            )
            return reused

    _enforce_upload_rate(user)

    # Persist the input blob and write the file BEFORE taking any quota lock, so the
    # UsageQuota row lock (below) is held only for the fast count + insert rather than the
    # whole multi-MB upload write. If anything downstream fails, this blob is cleaned up.
    input_blob = _persist_input_blob(workspace, display_name, detected_mime, uploaded_file)
    decision = None
    try:
        try:
            with transaction.atomic():
                # Admit under the UsageQuota row lock and insert the job in the SAME
                # transaction, so the active-job count and the insert are atomic (the
                # SELECT FOR UPDATE lock is held until commit, closing the count TOCTOU).
                decision = reserve_quota(
                    user=user, workspace=workspace, requested_bytes=uploaded_file.size
                )
                if decision.result != QuotaDecision.Result.ALLOWED:
                    raise _QuotaDenied(decision.reason)

                # get_or_create resolves a concurrent first-submit race internally: the
                # loser's unique-violation is caught within its savepoint and it returns the
                # winner with created=False (no IntegrityError reaches us).
                job, created = ConversionJob.objects.get_or_create(
                    owner=user,
                    workspace=workspace,
                    idempotency_key=idempotency_key,
                    defaults={
                        "organization": workspace.organization,
                        "source_format": source_format,
                        "target_format": target_format,
                        "detected_mime": detected_mime,
                        "status": ConversionJob.Status.PENDING,
                        "progress_mode": converter.progress_mode,
                        "converter_name": converter.converter_name,
                        "converter_version": converter.converter_version,
                        "option_payload": cleaned_options,
                        "option_schema_version": converter.option_schema().version,
                        "input_file": input_blob,
                        "original_display_filename": display_name,
                        "input_byte_size": uploaded_file.size,
                        "input_checksum": input_blob.sha256,
                        "malware_scan_verdict": _malware_scan_verdict(source_format, detected_mime),
                        "quota_decision": decision,
                        "queued_at": timezone.now(),
                    },
                )

                if created:
                    JobEvent.objects.create(
                        job=job, event_type="job.created", message="Job accepted"
                    )
                    write_audit(
                        organization=workspace.organization,
                        workspace=workspace,
                        actor=user,
                        event_type="conversion.job_created",
                        obj=job,
                        message=f"Created conversion job for {display_name}",
                    )
                    transaction.on_commit(lambda: enqueue_conversion_job(job.pk))
        except _QuotaDenied as exc:
            record_quota_denial(
                user=user,
                workspace=workspace,
                requested_bytes=uploaded_file.size,
                reason=exc.reason,
            )
            raise ValueError(f"Quota denied this upload: {exc.reason}") from None
    except Exception:
        # Denial or any other failure: the pre-persisted input blob is unreferenced. Drop
        # its row AND its stored file so nothing orphans on disk.
        _discard_blob(input_blob)
        raise

    if not created:
        _verify_idempotent_match(
            job,
            source_format=source_format,
            target_format=target_format,
            upload_checksum=input_blob.sha256,
            options=cleaned_options,
        )
        # Raced a concurrent duplicate: our input blob and quota decision are unreferenced.
        _discard_blob(input_blob)
        _discard_decision(decision)
    return job


def _malware_scan_verdict(source_format: str, detected_mime: str) -> str:
    if not settings.FILECONVERTER_ENFORCE_MIME_MATCH:
        return "not_required"
    if not mime_scanner_available():
        return "skipped_unavailable"
    try:
        validate_content_type(source_format, detected_mime)
    except ValueError:
        return "mime_failed"
    return "mime_passed"


def _verify_idempotent_match(
    job: ConversionJob,
    *,
    source_format: str,
    target_format: str,
    upload_checksum: str,
    options: dict,
) -> None:
    if job.source_format != source_format or job.target_format != target_format:
        raise ValueError(
            "This idempotency key was already used for a different conversion target."
        )
    if job.input_checksum and upload_checksum != job.input_checksum:
        raise ValueError("This idempotency key was already used for a different file.")
    if job.option_payload != options:
        raise ValueError(
            "This idempotency key was already used with different conversion options."
        )


def _find_reusable_job(
    *,
    user,
    workspace,
    source_format: str,
    target_format: str,
    upload_checksum: str,
    options: dict,
) -> ConversionJob | None:
    """Return a recent successful job with identical input/options when reuse is enabled."""
    ttl = output_ttl_hours(workspace.organization)
    cutoff = timezone.now() - timedelta(hours=ttl)
    candidates = ConversionJob.objects.filter(
        workspace=workspace,
        owner=user,
        status=ConversionJob.Status.DONE,
        source_format=source_format,
        target_format=target_format,
        input_checksum=upload_checksum,
        option_payload=options,
        output_file__isnull=False,
        finished_at__gte=cutoff,
    ).select_related("organization", "output_file").order_by("-finished_at")
    for job in candidates[:5]:
        if job.output_file and job.output_file.deleted_at is None:
            if job.expires_at and job.expires_at <= timezone.now():
                continue
            return job
    return None


class _QuotaDenied(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _persist_input_blob(workspace, display_name, detected_mime, uploaded_file) -> FileBlob:
    # Hash the upload buffer up front so a single INSERT persists a complete row (no window
    # where a crash could leave a blob with an empty checksum).
    checksum = sha256_upload(uploaded_file)
    input_blob = FileBlob(
        organization=workspace.organization,
        workspace=workspace,
        kind=FileBlob.Kind.INPUT,
        original_name=display_name,
        content_type=detected_mime or getattr(uploaded_file, "content_type", ""),
        byte_size=uploaded_file.size,
        sha256=checksum,
    )
    input_blob.file.save(display_name, uploaded_file, save=False)
    input_blob.save()
    return input_blob


def _discard_blob(input_blob: FileBlob | None) -> None:
    if input_blob is None:
        return
    try:
        input_blob.file.delete(save=False)
    except Exception:
        logger.exception("Failed to delete orphaned input blob file")
    try:
        input_blob.delete()
    except Exception:
        logger.exception("Failed to delete orphaned input blob row")


def _discard_decision(decision) -> None:
    try:
        if decision is not None:
            decision.delete()
    except Exception:
        logger.exception("Failed to delete orphaned quota decision")


def _revoke_celery_task(task_id: str) -> None:
    if not task_id:
        return
    try:
        from fileconverter.celery import app

        app.control.revoke(task_id, terminate=False)
    except Exception:
        logger.exception("Failed to revoke Celery task %s", task_id)


def enqueue_conversion_job(job_pk: int) -> None:
    from .tasks import process_conversion_job

    task = process_conversion_job.delay(job_pk)
    ConversionJob.objects.filter(pk=job_pk).update(celery_task_id=task.id)


@transaction.atomic
def claim_job(job_pk: int, *, worker_id: str | None = None) -> tuple[ConversionJob, int] | None:
    worker_id = worker_id or f"{platform.node()}:{os.getpid()}:{uuid.uuid4()}"
    now = timezone.now()
    job = ConversionJob.objects.select_for_update().filter(pk=job_pk).first()
    if not job:
        return None
    if job.cancel_requested:
        if job.status in {
            ConversionJob.Status.PENDING,
            ConversionJob.Status.RETRYING,
        }:
            _immediate_cancel(job)
        return None
    if job.status in ConversionJob.TERMINAL_STATUSES:
        return None
    stale_processing = job.status == ConversionJob.Status.PROCESSING and (
        (job.heartbeat_at and job.heartbeat_at < now - STALE_PROCESSING_AFTER)
        or (
            job.heartbeat_at is None
            and job.updated_at < now - STALE_PROCESSING_AFTER
        )
    )
    if (
        job.status not in {ConversionJob.Status.PENDING, ConversionJob.Status.RETRYING}
        and not stale_processing
    ):
        return None

    job.status = ConversionJob.Status.PROCESSING
    job.worker_id = worker_id
    job.claim_generation += 1
    job.attempt_count += 1
    job.started_at = job.started_at or now
    job.heartbeat_at = now
    job.progress_percent = max(job.progress_percent, 1)
    job.progress_message = "Worker claimed job"
    job.save(
        update_fields=[
            "status",
            "worker_id",
            "claim_generation",
            "attempt_count",
            "started_at",
            "heartbeat_at",
            "progress_percent",
            "progress_message",
            "updated_at",
        ]
    )
    JobEvent.objects.create(
        job=job,
        event_type="job.claimed",
        message="Worker claimed job",
        metadata={"claim_generation": job.claim_generation, "worker_id": worker_id},
    )
    return job, job.claim_generation


def heartbeat(job_pk: int, claim_generation: int) -> bool:
    return (
        ConversionJob.objects.filter(
            pk=job_pk,
            claim_generation=claim_generation,
            status=ConversionJob.Status.PROCESSING,
        ).update(heartbeat_at=timezone.now())
        == 1
    )


def request_cancel(job: ConversionJob, *, actor=None) -> ConversionJob:
    if job.is_terminal:
        return job
    job.refresh_from_db()
    if job.cancel_requested and job.status not in {
        ConversionJob.Status.PENDING,
        ConversionJob.Status.RETRYING,
    }:
        return job
    if job.status in {ConversionJob.Status.PENDING, ConversionJob.Status.RETRYING}:
        _immediate_cancel(job, actor=actor)
        job.refresh_from_db()
        return job
    updated = ConversionJob.objects.filter(
        pk=job.pk, cancel_requested=False
    ).update(cancel_requested=True, progress_message="Cancellation requested")
    if updated != 1:
        job.refresh_from_db()
        return job
    JobEvent.objects.create(
        job=job, event_type="job.cancel_requested", message="Cancellation requested"
    )
    write_audit(
        organization=job.organization,
        workspace=job.workspace,
        actor=actor,
        event_type="conversion.cancel_requested",
        obj=job,
        message="Cancellation requested",
    )
    job.refresh_from_db()
    return job


def _immediate_cancel(job: ConversionJob, *, actor=None) -> None:
    now = timezone.now()
    updated = ConversionJob.objects.filter(
        pk=job.pk,
        status__in={ConversionJob.Status.PENDING, ConversionJob.Status.RETRYING},
    ).update(
        status=ConversionJob.Status.CANCELLED,
        cancel_requested=True,
        finished_at=now,
        heartbeat_at=now,
        progress_message="Cancelled",
        progress_percent=0,
    )
    if updated != 1:
        return
    _revoke_celery_task(job.celery_task_id)
    JobEvent.objects.create(job=job, event_type="job.cancelled", message="Cancelled")
    write_audit(
        organization=job.organization,
        workspace=job.workspace,
        actor=actor,
        event_type="conversion.cancelled",
        obj=job,
        message="Cancellation completed",
    )
    enqueue_outbox(
        organization=job.organization,
        event_type="conversion.job.cancelled",
        idempotency_key=f"job:{job.public_id}:cancelled",
        payload={"job_id": str(job.public_id), "status": "cancelled"},
    )


def raise_if_cancelled(job_pk: int, claim_generation: int) -> None:
    job = ConversionJob.objects.only("cancel_requested", "claim_generation").get(pk=job_pk)
    if job.claim_generation != claim_generation:
        raise JobFenced("Job claim was superseded by a newer worker")
    if job.cancel_requested:
        raise JobCancelled("Job was cancelled")


def run_conversion(job_pk: int) -> None:
    claim = claim_job(job_pk)
    if not claim:
        return
    job, claim_generation = claim
    reporter = ProgressReporter(
        job.public_id, job_pk=job.pk, claim_generation=claim_generation
    )
    work_dir = None
    converter = None
    try:
        converter = registry.get(job.source_format, job.target_format)
        # Work-dir setup is inside the try so a failure here (permissions, disk full)
        # releases the claim via the normal failure path instead of stranding the job in
        # PROCESSING until the reaper window elapses.
        settings.FILECONVERTER_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        work_dir = Path(
            tempfile.mkdtemp(prefix=f"job-{job.public_id}-", dir=settings.FILECONVERTER_WORK_ROOT)
        )
        input_path = job.input_file.path
        output_path = work_dir / f"output.{job.target_format}"

        reporter(5, "Scanning input")
        raise_if_cancelled(job.pk, claim_generation)
        metadata = converter.probe(input_path)
        converter.validate_input(input_path, metadata)
        coarse_progress(job.pk, claim_generation, 10, "Input validated")
        heartbeat(job.pk, claim_generation)

        raise_if_cancelled(job.pk, claim_generation)
        result = converter.convert(
            input_path,
            output_path,
            job.target_format,
            job.option_payload,
            progress_callback=reporter,
        )
        heartbeat(job.pk, claim_generation)

        raise_if_cancelled(job.pk, claim_generation)
        converter.validate_output(output_path, result)
        reporter(96, "Promoting output")
        output_blob = create_output_blob(job, output_path)
        try:
            promote_output(job, claim_generation, output_blob)
        except Exception:
            # Promotion was fenced or cancelled: the output blob is now unreferenced.
            # Drop it immediately rather than waiting for the blob GC sweep.
            _discard_blob(output_blob)
            raise
        terminal_transition(
            job,
            claim_generation,
            ConversionJob.Status.DONE,
            progress_percent=100,
            message="Conversion complete",
        )
    except JobFenced:
        # A newer worker owns this job (expected under at-least-once delivery / stale
        # reclaim). Abort silently; the owning worker drives the job to completion.
        logger.info("Job %s attempt fenced by a newer claim; aborting", job.public_id)
        return
    except JobCancelled:
        terminal_transition(
            job,
            claim_generation,
            ConversionJob.Status.CANCELLED,
            progress_percent=0,
            message="Cancelled",
        )
    except UnsupportedFormatPair as exc:
        terminal_transition(
            job,
            claim_generation,
            ConversionJob.Status.FAILED,
            message=str(exc),
            error_code="unsupported_pair",
        )
    except TRANSIENT_EXCEPTIONS as exc:
        _handle_conversion_exception(
            job, claim_generation, exc, classification="transient"
        )
    except Exception as exc:
        _handle_conversion_exception(
            job, claim_generation, exc, classification="permanent"
        )
    finally:
        if work_dir is not None and converter is not None:
            converter.cleanup(work_dir)


def _handle_conversion_exception(
    job: ConversionJob, claim_generation: int, exc: Exception, *, classification: str
) -> None:
    error_detail = traceback.format_exc(limit=12)
    error_code = exc.__class__.__name__
    attempts_used = job.attempt_count  # already incremented by claim_job for this attempt
    can_retry = (
        classification == "transient" and attempts_used < settings.FILECONVERTER_MAX_ATTEMPTS
    )
    if can_retry and retry_transition(
        job,
        claim_generation,
        error_code=error_code,
        error_detail=error_detail,
        attempts_used=attempts_used,
    ):
        logger.warning(
            "Conversion job %s failed transiently (attempt %s/%s); scheduled retry",
            job.public_id,
            attempts_used,
            settings.FILECONVERTER_MAX_ATTEMPTS,
        )
        return
    logger.error(
        "Conversion job %s failed permanently (%s): %s",
        job.public_id,
        error_code,
        exc,
    )
    committed = terminal_transition(
        job,
        claim_generation,
        ConversionJob.Status.FAILED,
        message="Conversion failed cleanly",
        error_code=error_code,
        error_detail=error_detail,
    )
    # A retryable failure that exhausted its attempt budget is a dead-letter: surface it
    # loudly (ERROR log + dedicated outbox event) so operators/consumers can act on it.
    if committed and classification == "transient":
        emit_dead_letter(job.pk, job.public_id, job.organization_id, reason=error_code)


def emit_dead_letter(job_pk: int, public_id, organization_id: int, *, reason: str) -> None:
    logger.error(
        "DEAD-LETTER: conversion job %s exhausted retries (%s); manual attention needed",
        public_id,
        reason,
    )
    JobEvent.objects.create(
        job_id=job_pk,
        event_type="job.dead_letter",
        message=f"Exhausted retries: {reason}",
    )
    enqueue_outbox(
        organization_id=organization_id,
        event_type="conversion.job.dead_letter",
        idempotency_key=f"job:{public_id}:dead_letter",
        payload={"job_id": str(public_id), "reason": reason},
    )


def retry_transition(
    job: ConversionJob,
    claim_generation: int,
    *,
    error_code: str,
    error_detail: str,
    attempts_used: int,
) -> bool:
    """Fence-guarded transition PROCESSING -> RETRYING that re-enqueues the job."""
    if job.cancel_requested:
        terminal_transition(
            job,
            claim_generation,
            ConversionJob.Status.CANCELLED,
            progress_percent=0,
            message="Cancelled",
        )
        return False
    now = timezone.now()
    updated = ConversionJob.objects.filter(
        pk=job.pk,
        claim_generation=claim_generation,
        status=ConversionJob.Status.PROCESSING,
        cancel_requested=False,
    ).update(
        status=ConversionJob.Status.RETRYING,
        retry_classification="transient",
        progress_message="Transient failure, retry scheduled",
        internal_error_code=error_code,
        internal_error_detail=error_detail,
        heartbeat_at=now,
    )
    if updated != 1:
        return False
    JobEvent.objects.create(
        job=job,
        event_type="job.retry_scheduled",
        message=f"Retry scheduled after {error_code}",
        metadata={"attempt": attempts_used, "error_code": error_code},
    )
    # Exponential-ish backoff capped for responsiveness.
    delay = min(60, 5 * (2 ** max(0, attempts_used - 1)))
    from .tasks import process_conversion_job

    transaction.on_commit(
        lambda: process_conversion_job.apply_async((job.pk,), countdown=delay)
    )
    return True


def coarse_progress(job_pk: int, claim_generation: int, percent: int, message: str) -> None:
    ConversionJob.objects.filter(
        pk=job_pk, claim_generation=claim_generation, status=ConversionJob.Status.PROCESSING
    ).update(progress_percent=percent, progress_message=message, heartbeat_at=timezone.now())


def create_output_blob(job: ConversionJob, output_path: Path) -> FileBlob:
    checksum = sha256_file(output_path)
    output_name = f"{Path(job.original_display_filename).stem}.{job.target_format}"
    with output_path.open("rb") as handle:
        blob = FileBlob(
            organization=job.organization,
            workspace=job.workspace,
            kind=FileBlob.Kind.OUTPUT,
            original_name=output_name,
            byte_size=output_path.stat().st_size,
            sha256=checksum,
        )
        blob.file.save(output_name, File(handle), save=False)
        blob.save()
    return blob


def promote_output(job: ConversionJob, claim_generation: int, output_blob: FileBlob) -> None:
    updated = ConversionJob.objects.filter(
        pk=job.pk,
        claim_generation=claim_generation,
        status=ConversionJob.Status.PROCESSING,
        cancel_requested=False,
    ).update(
        output_file=output_blob,
        output_byte_size=output_blob.byte_size,
        output_checksum=output_blob.sha256,
        progress_percent=98,
        progress_message="Output validated",
    )
    if updated == 1:
        return
    current = ConversionJob.objects.filter(pk=job.pk).only(
        "claim_generation", "cancel_requested", "status"
    ).first()
    if current is None:
        raise JobFenced("Job no longer exists")
    if current.claim_generation != claim_generation:
        raise JobFenced("Output promotion was fenced by a newer worker claim")
    if current.cancel_requested:
        raise JobCancelled("Output promotion was cancelled")
    raise JobFenced("Output promotion was fenced")


@transaction.atomic
def terminal_transition(
    job: ConversionJob,
    claim_generation: int,
    status: str,
    *,
    progress_percent: int | None = None,
    message: str = "",
    error_code: str = "",
    error_detail: str = "",
) -> bool:
    now = timezone.now()
    fields = {
        "status": status,
        "finished_at": now,
        "heartbeat_at": now,
        "progress_message": message,
        "internal_error_code": error_code,
        "internal_error_detail": error_detail,
    }
    if progress_percent is not None:
        fields["progress_percent"] = progress_percent
    if status == ConversionJob.Status.DONE:
        ttl_hours = output_ttl_hours(job.organization)
        fields["expires_at"] = now + timedelta(hours=ttl_hours)
    if status == ConversionJob.Status.FAILED:
        fields["failure_reason"] = message

    updated = ConversionJob.objects.filter(
        pk=job.pk, claim_generation=claim_generation, status=ConversionJob.Status.PROCESSING
    ).update(**fields)
    if updated != 1:
        return False

    JobEvent.objects.create(
        job=job, event_type=f"job.{status}", message=message, metadata={"error_code": error_code}
    )
    # Only successful conversions are billable; failed/cancelled jobs are not charged.
    if status == ConversionJob.Status.DONE:
        UsageLedger.objects.create(
            organization=job.organization,
            workspace=job.workspace,
            user=job.owner,
            quantity=job.input_byte_size,
            unit="bytes",
            reason=f"conversion.{status}",
            subject_id=str(job.public_id),
        )
    enqueue_outbox(
        organization=job.organization,
        event_type=f"conversion.job.{status}",
        idempotency_key=f"job:{job.public_id}:{status}",
        payload={"job_id": str(job.public_id), "status": status},
    )
    if status == ConversionJob.Status.DONE:
        ttl_hours = output_ttl_hours(job.organization)
        if ttl_hours == 0:
            _purge_expired_output(job.pk)
    return True


def _purge_expired_output(job_pk: int) -> None:
    """Delete output bytes for a single job and mark it EXPIRED (immediate TTL)."""
    now = timezone.now()
    job = ConversionJob.objects.filter(pk=job_pk).select_related("output_file").first()
    if job is None or job.status != ConversionJob.Status.DONE:
        return
    with transaction.atomic():
        updated = ConversionJob.objects.filter(
            pk=job.pk, status=ConversionJob.Status.DONE
        ).update(
            status=ConversionJob.Status.EXPIRED,
            output_file=None,
            progress_message="Output expired and purged",
        )
        if updated != 1:
            return
        blob = job.output_file
        if blob and blob.deleted_at is None:
            try:
                blob.file.delete(save=False)
            except Exception:
                logger.exception("Failed to delete immediate-expiry output for job %s", job.pk)
            blob.deleted_at = now
            blob.save(update_fields=["deleted_at"])
        JobEvent.objects.create(
            job_id=job.pk, event_type="job.expired", message="Output retention window elapsed"
        )


def requeue_stale_jobs() -> int:
    """Re-enqueue jobs stuck in PROCESSING whose worker stopped heart-beating.

    Intended to run periodically (Celery beat). ``claim_job`` performs the actual fenced
    takeover; here we flip the row back to RETRYING and dispatch it. A job that has already
    burned its attempt budget (e.g. a poison input that reliably wedges its worker) is
    failed instead of being requeued forever.
    """
    threshold = timezone.now() - STALE_PROCESSING_AFTER
    stale_filter = models.Q(status=ConversionJob.Status.PROCESSING) & (
        models.Q(heartbeat_at__lt=threshold)
        | models.Q(heartbeat_at__isnull=True, updated_at__lt=threshold)
    )
    stale = list(
        ConversionJob.objects.filter(stale_filter).values_list(
            "pk", "attempt_count", "public_id", "organization_id", "cancel_requested"
        )[:MAINTENANCE_BATCH_SIZE]
    )
    requeued = 0
    for job_pk, attempt_count, public_id, organization_id, cancel_requested in stale:
        if cancel_requested:
            with transaction.atomic():
                job = ConversionJob.objects.select_for_update().filter(pk=job_pk).first()
                if (
                    job
                    and job.cancel_requested
                    and job.status == ConversionJob.Status.PROCESSING
                ):
                    now = timezone.now()
                    ConversionJob.objects.filter(pk=job_pk).update(
                        status=ConversionJob.Status.CANCELLED,
                        finished_at=now,
                        heartbeat_at=now,
                        progress_message="Cancelled",
                        progress_percent=0,
                    )
                    _revoke_celery_task(job.celery_task_id)
                    JobEvent.objects.create(
                        job_id=job_pk,
                        event_type="job.cancelled",
                        message="Cancelled after stale worker",
                    )
            continue
        if attempt_count >= settings.FILECONVERTER_MAX_ATTEMPTS:
            with transaction.atomic():
                now = timezone.now()
                failed = ConversionJob.objects.filter(
                    pk=job_pk,
                    status=ConversionJob.Status.PROCESSING,
                ).filter(
                    models.Q(heartbeat_at__lt=threshold)
                    | models.Q(heartbeat_at__isnull=True, updated_at__lt=threshold)
                ).update(
                    status=ConversionJob.Status.FAILED,
                    retry_classification="stale_worker",
                    failure_reason="Abandoned after repeated worker timeouts",
                    progress_message="Failed after repeated worker timeouts",
                    progress_percent=0,
                    finished_at=now,
                    heartbeat_at=now,
                )
                if failed != 1:
                    continue
                JobEvent.objects.create(
                    job_id=job_pk,
                    event_type="job.failed",
                    message="Failed after exhausting stale-worker retries",
                )
                enqueue_outbox(
                    organization_id=organization_id,
                    event_type="conversion.job.failed",
                    idempotency_key=f"job:{public_id}:failed",
                    payload={"job_id": str(public_id), "status": "failed"},
                )
                emit_dead_letter(
                    job_pk, public_id, organization_id, reason="stale_worker_exhausted"
                )
            continue
        updated = ConversionJob.objects.filter(
            pk=job_pk,
            status=ConversionJob.Status.PROCESSING,
        ).filter(
            models.Q(heartbeat_at__lt=threshold)
            | models.Q(heartbeat_at__isnull=True, updated_at__lt=threshold)
        ).update(
            status=ConversionJob.Status.RETRYING,
            retry_classification="stale_worker",
            progress_message="Requeued after stale worker",
        )
        if updated == 1:
            JobEvent.objects.create(
                job_id=job_pk,
                event_type="job.requeued_stale",
                message="Requeued after worker heartbeat timeout",
            )
            enqueue_conversion_job(job_pk)
            requeued += 1
    if requeued:
        logger.info("Requeued %s stale conversion job(s)", requeued)
    return requeued


def expire_due_outputs() -> int:
    """Delete outputs past their retention window and mark the job EXPIRED."""
    now = timezone.now()
    due = list(
        ConversionJob.objects.filter(
            status=ConversionJob.Status.DONE, expires_at__isnull=False, expires_at__lte=now
        ).select_related("output_file")[:MAINTENANCE_BATCH_SIZE]
    )
    expired = 0
    for job in due:
        with transaction.atomic():
            updated = ConversionJob.objects.filter(
                pk=job.pk, status=ConversionJob.Status.DONE, expires_at__lte=now
            ).update(
                status=ConversionJob.Status.EXPIRED,
                output_file=None,
                progress_message="Output expired and purged",
            )
            if updated != 1:
                continue
            blob = job.output_file
            if blob and blob.deleted_at is None:
                try:
                    blob.file.delete(save=False)
                except Exception:
                    logger.exception(
                        "Failed to delete expired output file for job %s", job.public_id
                    )
                blob.deleted_at = now
                blob.save(update_fields=["deleted_at"])
            JobEvent.objects.create(
                job=job, event_type="job.expired", message="Output retention window elapsed"
            )
        expired += 1
    if expired:
        logger.info("Expired %s conversion output(s)", expired)
    return expired


def purge_terminal_input_files() -> int:
    """Reclaim stored INPUT file bytes for terminal jobs past the input retention window.

    The FileBlob row is kept (the input_file FK is PROTECT and rows are useful for audit);
    only the bytes on disk are deleted and ``deleted_at`` is stamped. Without this, input
    files accumulate for the lifetime of every job.
    """
    cutoff = timezone.now() - timedelta(hours=settings.FILECONVERTER_INPUT_TTL_HOURS)
    jobs = list(
        ConversionJob.objects.filter(
            status__in=ConversionJob.TERMINAL_STATUSES,
            input_file__isnull=False,
            input_file__deleted_at__isnull=True,
            created_at__lt=cutoff,
        )
        .filter(models.Q(finished_at__lt=cutoff) | models.Q(finished_at__isnull=True))
        .select_related("input_file")[:MAINTENANCE_BATCH_SIZE]
    )
    purged = 0
    now = timezone.now()
    for job in jobs:
        blob = job.input_file
        if blob is None or blob.deleted_at is not None:
            continue
        try:
            blob.file.delete(save=False)
        except Exception:
            logger.exception("Failed to delete expired input file for job %s", job.public_id)
        blob.deleted_at = now
        blob.save(update_fields=["deleted_at"])
        purged += 1
    if purged:
        logger.info("Purged %s expired input file(s)", purged)
    return purged


def garbage_collect_blobs() -> int:
    """Delete FileBlob rows (and their bytes) referenced by no job, past the GC window.

    Covers orphans that no other task reclaims: an input blob persisted just before a crash
    (it is committed before the job row), and an output blob created just before a fenced or
    cancelled ``promote_output`` (never linked to its job).
    """
    cutoff = timezone.now() - timedelta(hours=settings.FILECONVERTER_BLOB_GC_TTL_HOURS)
    orphans = list(
        FileBlob.objects.filter(
            created_at__lt=cutoff,
            input_jobs__isnull=True,
            output_jobs__isnull=True,
            batch_zips__isnull=True,
        )[:MAINTENANCE_BATCH_SIZE]
    )
    removed = 0
    for blob in orphans:
        try:
            if blob.file:
                blob.file.delete(save=False)
        except Exception:
            logger.exception("Failed to delete orphan blob file %s", blob.public_id)
        try:
            blob.delete()
            removed += 1
        except Exception:
            logger.exception("Failed to delete orphan blob row %s", blob.public_id)
    if removed:
        logger.info("Garbage-collected %s orphan blob(s)", removed)
    return removed
