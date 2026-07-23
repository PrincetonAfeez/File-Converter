# "ConversionJob and JobEvent models."
from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class ConversionBatch(models.Model):
    """Batch ZIP scaffolding — not exposed in the product UI (admin read-only)."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        PARTIAL = "partial", "Partial success"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey("organizations.Workspace", on_delete=models.CASCADE)
    target_format = models.CharField(max_length=24, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    total_jobs = models.PositiveIntegerField(default=0)
    completed_jobs = models.PositiveIntegerField(default=0)
    failed_jobs = models.PositiveIntegerField(default=0)
    cancelled_jobs = models.PositiveIntegerField(default=0)
    batch_zip = models.ForeignKey(
        "files.FileBlob",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="batch_zips",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)


class ConversionJobQuerySet(models.QuerySet):
    def accessible_to(self, user):
        """Structurally scope to the tenant boundary: jobs in orgs the user belongs to.

        This enforces cross-tenant (cross-org) isolation at the query layer rather than
        relying on each view remembering to filter. Workspace-level ACL within an org is a
        further check applied by ``user_can_access_workspace``.
        """
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if user.is_superuser:
            return self
        from apps.organizations.models import Membership, Organization

        org_ids = Membership.objects.filter(
            user=user,
            status=Membership.Status.ACTIVE,
            organization__status=Organization.Status.ACTIVE,
        ).values("organization_id")
        return self.filter(organization_id__in=org_ids)


class ConversionJob(models.Model):
    objects = ConversionJobQuerySet.as_manager()

    class Status(models.TextChoices):
        # UPLOADED/SCANNING/SCAN_FAILED are reserved for a future scan pipeline; unused today.
        UPLOADED = "uploaded", "Uploaded"
        SCANNING = "scanning", "Scanning"
        SCAN_FAILED = "scan_failed", "Scan failed"
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        RETRYING = "retrying", "Retrying"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    TERMINAL_STATUSES = {
        Status.SCAN_FAILED,
        Status.DONE,
        Status.FAILED,
        Status.CANCELLED,
        Status.EXPIRED,
    }

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey("organizations.Workspace", on_delete=models.CASCADE)
    batch = models.ForeignKey(
        ConversionBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    source_format = models.CharField(max_length=24)
    target_format = models.CharField(max_length=24)
    detected_mime = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    progress_mode = models.CharField(max_length=24, default="indeterminate")
    progress_percent = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    progress_message = models.CharField(max_length=160, blank=True)
    converter_name = models.CharField(max_length=80)
    converter_version = models.CharField(max_length=40)
    option_payload = models.JSONField(default=dict, blank=True)
    option_schema_version = models.CharField(max_length=40, default="1")
    celery_task_id = models.CharField(max_length=255, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    retry_classification = models.CharField(max_length=60, blank=True)
    failure_reason = models.CharField(max_length=240, blank=True)
    internal_error_code = models.CharField(max_length=80, blank=True)
    internal_error_detail = models.TextField(blank=True)
    input_file = models.ForeignKey(
        "files.FileBlob", on_delete=models.PROTECT, related_name="input_jobs"
    )
    output_file = models.ForeignKey(
        "files.FileBlob",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="output_jobs",
    )
    original_display_filename = models.CharField(max_length=255)
    input_byte_size = models.PositiveBigIntegerField(default=0)
    output_byte_size = models.PositiveBigIntegerField(default=0)
    input_checksum = models.CharField(max_length=64, blank=True)
    output_checksum = models.CharField(max_length=64, blank=True)
    malware_scan_verdict = models.CharField(max_length=40, default="not_configured")
    quota_decision = models.ForeignKey(
        "quotas.QuotaDecision", null=True, blank=True, on_delete=models.SET_NULL
    )
    idempotency_key = models.CharField(max_length=160)
    worker_id = models.CharField(max_length=120, blank=True)
    claim_generation = models.PositiveIntegerField(default=0)
    cancel_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["workspace", "-created_at"]),
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["status", "updated_at"]),
            models.Index(fields=["expires_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "workspace", "idempotency_key"],
                name="uniq_job_idempotency_per_actor_workspace",
            )
        ]
        ordering = ["-created_at"]

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    @property
    def output_downloadable(self) -> bool:
        if self.status != self.Status.DONE or not self.output_file_id:
            return False
        output = self.output_file
        if output.deleted_at is not None:
            return False
        return not (self.expires_at and self.expires_at <= timezone.now())

    def __str__(self) -> str:
        return f"{self.original_display_filename}: {self.source_format}->{self.target_format}"


class JobEvent(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="job_events"
    )
    job = models.ForeignKey(ConversionJob, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=80)
    message = models.CharField(max_length=260, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def save(self, *args, **kwargs):
        if self.organization_id is None and self.job_id:
            org_id = ConversionJob.objects.filter(pk=self.job_id).values_list(
                "organization_id", flat=True
            ).first()
            if org_id:
                self.organization_id = org_id
        super().save(*args, **kwargs)
