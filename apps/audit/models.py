# "AuditEvent and OutboxEvent persistence models."
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        "organizations.Workspace", null=True, blank=True, on_delete=models.SET_NULL
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    event_type = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=80, blank=True)
    message = models.CharField(max_length=300, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "created_at"])]
        ordering = ["-created_at"]


class OutboxEvent(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE)
    event_type = models.CharField(max_length=80)
    idempotency_key = models.CharField(max_length=160, unique=True)
    payload = models.JSONField(default=dict)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["event_type", "delivered_at"]),
            models.Index(fields=["delivered_at", "created_at"]),
            models.Index(fields=["failed_at"], name="audit_outbo_failed__a1b2c3_idx"),
        ]
