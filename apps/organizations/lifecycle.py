# "GDPR export and account deletion flows."
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from .models import Membership, Organization

logger = logging.getLogger(__name__)


def delete_organization(org: Organization) -> None:
    """Hard-delete a tenant: purge its stored files, then all of its rows.

    Deletes in FK-safe order (jobs reference input blobs via PROTECT), then cascades the
    remainder (workspaces, memberships, quotas, ledger, audit, outbox) via org.delete().
    """
    from apps.conversions.models import ConversionJob
    from apps.files.models import FileBlob

    with transaction.atomic():
        for blob in FileBlob.objects.filter(organization=org):
            try:
                blob.file.delete(save=False)
            except Exception:
                logger.exception("Failed to delete file for blob %s during org delete", blob.pk)
        ConversionJob.objects.filter(organization=org).delete()
        FileBlob.objects.filter(organization=org).delete()
        org.delete()
    logger.info("Deleted organization %s and all tenant data", org.public_id)


def _scrub_user_artifacts_in_shared_org(user) -> None:
    """Anonymize or purge user-owned data in orgs that survive account deletion."""
    from apps.audit.models import AuditEvent
    from apps.conversions.models import ConversionJob, JobEvent
    from apps.quotas.models import QuotaDecision, UsageLedger

    shared_org_ids = []
    for membership in Membership.objects.filter(user=user).select_related("organization"):
        org = membership.organization
        if Membership.objects.filter(organization=org).exclude(user=user).exists():
            shared_org_ids.append(org.pk)

    if not shared_org_ids:
        return

    owned_jobs = ConversionJob.objects.filter(owner=user, organization_id__in=shared_org_ids)
    blob_ids = set()
    for job in owned_jobs.select_related("input_file", "output_file"):
        for blob in (job.input_file, job.output_file):
            if blob and blob.deleted_at is None:
                blob_ids.add(blob.pk)
                try:
                    blob.file.delete(save=False)
                except Exception:
                    logger.exception("Failed to delete blob file during user scrub")
                blob.deleted_at = timezone.now()
                blob.original_name = "deleted"
                blob.save(update_fields=["deleted_at", "original_name"])

    owned_jobs.update(
        original_display_filename="deleted",
        internal_error_detail="",
        progress_message="",
        failure_reason="",
    )
    JobEvent.objects.filter(job__owner=user, job__organization_id__in=shared_org_ids).update(
        message="[redacted]"
    )
    AuditEvent.objects.filter(actor=user, organization_id__in=shared_org_ids).update(
        message="[redacted]"
    )
    QuotaDecision.objects.filter(user=user, organization_id__in=shared_org_ids).delete()
    UsageLedger.objects.filter(user=user, organization_id__in=shared_org_ids).delete()


def delete_user_account(user) -> None:
    """Delete a user and any org they solely own; scrub artifacts in shared orgs."""
    with transaction.atomic():
        _scrub_user_artifacts_in_shared_org(user)
        for membership in Membership.objects.filter(user=user).select_related("organization"):
            org = membership.organization
            has_others = (
                Membership.objects.filter(organization=org).exclude(user=user).exists()
            )
            if not has_others:
                delete_organization(org)
        user.delete()
    logger.info("Deleted user account %s", getattr(user, "pk", "?"))


def export_user_data(user) -> dict:
    """Return a JSON-serializable export of a user's account and conversion history."""
    from apps.audit.models import AuditEvent, OutboxEvent
    from apps.conversions.models import ConversionJob, JobEvent
    from apps.files.models import FileBlob
    from apps.organizations.models import WorkspaceMembership
    from apps.quotas.models import QuotaDecision, UsageLedger, UsageQuota

    memberships = list(Membership.objects.filter(user=user).select_related("organization"))
    orgs = [
        {
            "public_id": str(m.organization.public_id),
            "name": m.organization.name,
            "role": m.role,
            "membership_status": m.status,
            "organization_status": m.organization.status,
            "created_at": m.created_at.isoformat(),
            "workspaces": [
                {"public_id": str(ws.public_id), "name": ws.name}
                for ws in m.organization.workspaces.all()
            ],
        }
        for m in memberships
    ]
    workspace_memberships = [
        {
            "workspace_public_id": str(wm.workspace.public_id),
            "workspace_name": wm.workspace.name,
            "organization_public_id": str(wm.workspace.organization.public_id),
            "role": wm.role,
            "created_at": wm.created_at.isoformat(),
        }
        for wm in WorkspaceMembership.objects.filter(user=user).select_related(
            "workspace", "workspace__organization"
        )
    ]
    org_ids = [m.organization_id for m in memberships]
    usage_quotas = [
        {
            "workspace_public_id": str(q.workspace.public_id),
            "max_upload_bytes": q.max_upload_bytes,
            "max_active_jobs": q.max_active_jobs,
        }
        for q in UsageQuota.objects.filter(organization_id__in=org_ids).select_related("workspace")
    ]
    owned_jobs = list(ConversionJob.objects.filter(owner=user).order_by("created_at"))
    job_public_ids = [j.public_id for j in owned_jobs]
    jobs = [
        {
            "public_id": str(j.public_id),
            "filename": j.original_display_filename,
            "source_format": j.source_format,
            "target_format": j.target_format,
            "status": j.status,
            "input_byte_size": j.input_byte_size,
            "output_byte_size": j.output_byte_size,
            "input_checksum": j.input_checksum,
            "output_checksum": j.output_checksum,
            "created_at": j.created_at.isoformat(),
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        }
        for j in owned_jobs
    ]
    job_events = [
        {
            "public_id": str(e.public_id),
            "job_public_id": str(e.job.public_id),
            "event_type": e.event_type,
            "message": e.message,
            "metadata": e.metadata,
            "created_at": e.created_at.isoformat(),
        }
        for e in JobEvent.objects.filter(job__public_id__in=job_public_ids)
        .select_related("job")
        .order_by("created_at")
    ]
    blob_ids = {
        blob_id
        for j in owned_jobs
        for blob_id in (j.input_file_id, j.output_file_id)
        if blob_id
    }
    blobs = [
        {
            "public_id": str(b.public_id),
            "kind": b.kind,
            "original_name": b.original_name,
            "byte_size": b.byte_size,
            "sha256": b.sha256,
            "created_at": b.created_at.isoformat(),
            "deleted_at": b.deleted_at.isoformat() if b.deleted_at else None,
        }
        for b in FileBlob.objects.filter(pk__in=blob_ids).order_by("created_at")
    ]
    audit_events = [
        {
            "public_id": str(e.public_id),
            "event_type": e.event_type,
            "message": e.message,
            "object_type": e.object_type,
            "object_id": e.object_id,
            "created_at": e.created_at.isoformat(),
        }
        for e in AuditEvent.objects.filter(actor=user).order_by("created_at")
    ]
    quota_decisions = [
        {
            "public_id": str(d.public_id),
            "result": d.result,
            "reason": d.reason,
            "requested_bytes": d.requested_bytes,
            "created_at": d.created_at.isoformat(),
        }
        for d in QuotaDecision.objects.filter(user=user).order_by("created_at")
    ]
    usage_ledger = [
        {
            "public_id": str(entry.public_id),
            "quantity": entry.quantity,
            "unit": entry.unit,
            "reason": entry.reason,
            "subject_id": entry.subject_id,
            "created_at": entry.created_at.isoformat(),
        }
        for entry in UsageLedger.objects.filter(user=user).order_by("created_at")
    ]
    outbox_events = [
        {
            "public_id": str(e.public_id),
            "event_type": e.event_type,
            "idempotency_key": e.idempotency_key,
            "payload": e.payload,
            "delivered_at": e.delivered_at.isoformat() if e.delivered_at else None,
            "failed_at": e.failed_at.isoformat() if e.failed_at else None,
            "created_at": e.created_at.isoformat(),
        }
        for e in OutboxEvent.objects.filter(organization_id__in=org_ids).order_by("created_at")
    ]
    return {
        "user": {
            "username": user.get_username(),
            "email": user.email,
            "date_joined": user.date_joined.isoformat(),
        },
        "organizations": orgs,
        "workspace_memberships": workspace_memberships,
        "usage_quotas": usage_quotas,
        "conversion_jobs": jobs,
        "job_events": job_events,
        "file_blobs": blobs,
        "audit_events": audit_events,
        "quota_decisions": quota_decisions,
        "usage_ledger": usage_ledger,
        "outbox_events": outbox_events,
        "export_notes": {
            "job_count": len(jobs),
            "includes_checksums": True,
            "binary_payloads_excluded": True,
            "usage_ledger_note": (
                "UsageLedger records billable bytes; cumulative period quotas are not enforced "
                "at admission time (see apps/quotas/services.py)."
            ),
        },
    }
