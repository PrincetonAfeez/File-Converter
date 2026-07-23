# "FeatureFlag and related ops models."
from __future__ import annotations

from django.db import models


class FeatureFlag(models.Model):
    """Runtime-togglable feature flag for safe rollouts (kill-switch / gradual enable).

    Resolution order (see apps.ops.flags.flag_enabled): settings override → DB row →
    default. DB rows let operators flip a flag without a deploy.
    """

    name = models.SlugField(max_length=80, unique=True)
    enabled = models.BooleanField(default=False)
    description = models.CharField(max_length=240, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name}={'on' if self.enabled else 'off'}"
