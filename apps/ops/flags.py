# "Feature flag resolution helpers."
from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_CACHE_TTL = 30
_SENTINEL = object()


def flag_enabled(name: str, default: bool = False) -> bool:
    """Resolve a feature flag: settings override → DB row (cached) → default.

    Use for safe rollouts and kill-switches. ``FILECONVERTER_FLAGS`` in settings is an
    env/deploy-level override map; the DB row lets operators flip without a deploy.
    """
    overrides = getattr(settings, "FILECONVERTER_FLAGS", {})
    if name in overrides:
        return bool(overrides[name])

    cache_key = f"flag:{name}"
    cached = cache.get(cache_key, _SENTINEL)
    if cached is not _SENTINEL:
        return cached

    value = default
    try:
        from .models import FeatureFlag

        row = FeatureFlag.objects.filter(name=name).only("enabled").first()
        if row is not None:
            value = row.enabled
    except Exception:  # DB unavailable / not migrated: fall back to default
        logger.exception("Feature flag lookup failed for %s", name)
        return default
    cache.set(cache_key, value, _CACHE_TTL)
    return value
