# "Organization, workspace, and membership models."
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils.text import slugify

SLUG_MAX_LENGTH = 170


def _unique_slug(model, value: str, *, pk=None, scope: dict | None = None) -> str:
    """Return a slug unique for ``model`` (optionally within ``scope``), truncated safely."""
    base = slugify(value)[:SLUG_MAX_LENGTH] or "item"
    slug = base
    counter = 1
    queryset = model.objects.all()
    if scope:
        queryset = queryset.filter(**scope)
    if pk is not None:
        queryset = queryset.exclude(pk=pk)
    while queryset.filter(slug=slug).exists():
        counter += 1
        suffix = f"-{counter}"
        slug = f"{base[: SLUG_MAX_LENGTH - len(suffix)]}{suffix}"
    return slug


class Organization(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ACTIVE)
    default_output_ttl_hours = models.PositiveIntegerField(default=24)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Organization, self.name, pk=self.pk)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Workspace(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="workspaces"
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180)
    allow_output_reuse = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"], name="uniq_workspace_slug_per_org"
            )
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(
                Workspace,
                self.name,
                pk=self.pk,
                scope={"organization": self.organization},
            )
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.organization} / {self.name}"


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        AUDITOR = "auditor", "Auditor"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INVITED = "invited", "Invited"
        DISABLED = "disabled", "Disabled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=24, choices=Role.choices, default=Role.MEMBER)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"], name="uniq_membership_user_org"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.organization} ({self.role})"


class WorkspaceMembership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workspace_memberships"
    )
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(
        max_length=24, choices=Membership.Role.choices, default=Membership.Role.MEMBER
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "workspace"], name="uniq_workspace_member")
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.workspace}"
