# "Validate required production environment variables."
"""Production deploy environment validation (DEPLOY1 / DEPLOY2)."""

from __future__ import annotations

import os


def _truthy(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def validate_deploy_env(*, require_uptime_url: bool = False) -> list[str]:
    """Return human-readable errors for a missing production control (empty = ok)."""
    errors: list[str] = []
    debug = _truthy("DJANGO_DEBUG", False)
    require_sentry = _truthy("FILECONVERTER_REQUIRE_SENTRY", not debug)

    if require_sentry and not os.environ.get("SENTRY_DSN", "").strip():
        errors.append(
            "SENTRY_DSN is required when FILECONVERTER_REQUIRE_SENTRY is enabled "
            "(default when DJANGO_DEBUG=False). Set the DSN or opt out with "
            "FILECONVERTER_REQUIRE_SENTRY=False."
        )

    if require_uptime_url and not os.environ.get("STAGING_BASE_URL", "").strip():
        errors.append(
            "STAGING_BASE_URL is required for synthetic uptime paging. "
            "Set the GitHub Actions secret (or env var) to the staging base URL."
        )

    return errors
