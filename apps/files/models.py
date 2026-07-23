# "FileBlob storage model."
from __future__ import annotations

import uuid
from pathlib import Path

from django.db import models


def blob_upload_to(instance: FileBlob, filename: str) -> str:
    tenant = instance.workspace.organization.public_id if instance.workspace_id else "unscoped"
    workspace = instance.workspace.public_id if instance.workspace_id else "unscoped"
    suffix = Path(filename).suffix.lower()
    return f"tenants/{tenant}/workspaces/{workspace}/{instance.kind}/{instance.public_id}{suffix}"


class FileBlob(models.Model):
    class Kind(models.TextChoices):
        INPUT = "input", "Input"
        OUTPUT = "output", "Output"
        TEMP = "temp", "Temporary"
        BATCH_ZIP = "batch_zip", "Batch ZIP"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey("organizations.Workspace", on_delete=models.CASCADE)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    file = models.FileField(upload_to=blob_upload_to, max_length=600)
    original_name = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=160, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    encryption = models.CharField(max_length=64, default="local-dev")
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "workspace", "sha256"]),
            models.Index(fields=["kind", "created_at"]),
        ]

    @property
    def path(self) -> Path:
        return Path(self.file.path)

    def __str__(self) -> str:
        return f"{self.kind}:{self.original_name or self.public_id}"
