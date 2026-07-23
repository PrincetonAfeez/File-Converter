# "Deploy-time Django system checks (Sentry)."
"""Django system checks for production deploy configuration."""

from __future__ import annotations

import os

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def check_sentry_dsn_for_deploy(app_configs, **kwargs):
    """Require SENTRY_DSN when serving with DEBUG=False (LAUNCH4 / DEPLOY2).

    Opt out explicitly with FILECONVERTER_REQUIRE_SENTRY=False for ephemeral
    environments that still run ``check --deploy``.
    """
    if settings.DEBUG:
        return []
    if not getattr(settings, "FILECONVERTER_REQUIRE_SENTRY", True):
        return []
    if os.environ.get("SENTRY_DSN", "").strip():
        return []
    return [
        Error(
            "SENTRY_DSN is not set while DJANGO_DEBUG=False.",
            hint=(
                "Set SENTRY_DSN in the deployment environment so errors are captured, "
                "or set FILECONVERTER_REQUIRE_SENTRY=False to acknowledge the risk."
            ),
            id="fileconverter.E001",
        )
    ]
