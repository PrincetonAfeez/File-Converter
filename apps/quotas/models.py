# "UsageQuota, QuotaDecision, and UsageLedger models."
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class UsageQuota(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        "organizations.Workspace", null=True, blank=True, on_delete=models.CASCADE
    )
    max_upload_bytes = models.PositiveBigIntegerField(default=50 * 1024 * 1024)
    max_active_jobs = models.PositiveIntegerField(default=5)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace"],
                name="uniq_usagequota_org_workspace",
            )
        ]


class QuotaDecision(models.Model):
    class Result(models.TextChoices):
        ALLOWED = "allowed", "Allowed"
        DENIED = "denied", "Denied"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey("organizations.Workspace", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    result = models.CharField(max_length=24, choices=Result.choices)
    reason = models.CharField(max_length=240, blank=True)
    requested_bytes = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class UsageLedger(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey("organizations.Workspace", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    quantity = models.BigIntegerField()
    unit = models.CharField(max_length=32)
    reason = models.CharField(max_length=120)
    subject_id = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
