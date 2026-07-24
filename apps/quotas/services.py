# "Quota admission and retention services."
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.conversions.models import ConversionJob

from .models import QuotaDecision, UsageQuota

logger = logging.getLogger(__name__)


@transaction.atomic
def reserve_quota(*, user, workspace, requested_bytes: int) -> QuotaDecision:
    """Admission-control check for a new upload.

    Enforces per-file size and concurrent active-job limits only. ``UsageLedger`` records
    billable bytes after successful conversions but is **not** consulted here for cumulative
    daily/monthly totals — add that enforcement separately if product policy requires it.
    """
    quota, _ = UsageQuota.objects.select_for_update().get_or_create(
        organization=workspace.organization,
        workspace=workspace,
        defaults={"max_upload_bytes": settings.FILECONVERTER_MAX_UPLOAD_BYTES},
    )
    if requested_bytes > quota.max_upload_bytes:
        return QuotaDecision.objects.create(
            organization=workspace.organization,
            workspace=workspace,
            user=user,
            result=QuotaDecision.Result.DENIED,
            reason="upload_too_large",
            requested_bytes=requested_bytes,
        )

    active_jobs = ConversionJob.objects.filter(
        workspace=workspace,
        status__in=[
            ConversionJob.Status.PENDING,
            ConversionJob.Status.PROCESSING,
            ConversionJob.Status.RETRYING,
        ],
    ).count()
    if active_jobs >= quota.max_active_jobs:
        return QuotaDecision.objects.create(
            organization=workspace.organization,
            workspace=workspace,
            user=user,
            result=QuotaDecision.Result.DENIED,
            reason="too_many_active_jobs",
            requested_bytes=requested_bytes,
        )

    return QuotaDecision.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        user=user,
        result=QuotaDecision.Result.ALLOWED,
        requested_bytes=requested_bytes,
    )


def record_quota_denial(*, user, workspace, requested_bytes: int, reason: str) -> QuotaDecision:
    """Persist a standalone DENIED decision for audit (used when the admitting transaction
    is rolled back, so the in-transaction decision row does not survive)."""
    return QuotaDecision.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        user=user,
        result=QuotaDecision.Result.DENIED,
        reason=reason,
        requested_bytes=requested_bytes,
    )


def release_or_record_usage(*, job):
    # Placeholder for ledger writeback; explicit so callers do not forget the lifecycle hook.
    return None


def purge_expired_quota_decisions(*, older_than_days: int | None = None) -> int:
    """Delete QuotaDecision audit rows past their retention window.

    QuotaDecision rows are written on every admission check (including denials); without
    retention the table grows unbounded. Runs periodically via Celery beat.
    """
    if older_than_days is None:
        older_than_days = settings.FILECONVERTER_QUOTA_DECISION_TTL_DAYS
    cutoff = timezone.now() - timezone.timedelta(days=older_than_days)
    deleted, _ = QuotaDecision.objects.filter(created_at__lt=cutoff).delete()
    if deleted:
        logger.info("Purged %s expired quota decision(s)", deleted)
    return deleted
