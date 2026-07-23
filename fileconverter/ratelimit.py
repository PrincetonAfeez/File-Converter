# "Cache-backed rate limit counters."
from __future__ import annotations

from django.core.cache import cache


def increment_counter(key: str, *, window_seconds: int) -> int:
    """Atomically increment a rolling-window counter and return the new value."""
    if cache.add(key, 1, window_seconds):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window_seconds)
        return 1


def counter_value(key: str) -> int:
    return int(cache.get(key, 0))


def rate_limit_would_exceed(key: str, *, limit: int) -> bool:
    """Return True when the next increment would exceed ``limit`` (non-mutating peek)."""
    if limit <= 0:
        return False
    return counter_value(key) >= limit


def rate_limit_commit(key: str, *, window_seconds: int) -> int:
    """Record one allowed request against the rolling window."""
    return increment_counter(key, window_seconds=window_seconds)


def rate_limit_exceeded(key: str, *, limit: int, window_seconds: int) -> bool:
    """Increment and return True when ``limit`` is exceeded (legacy helper)."""
    if limit <= 0:
        return False
    return increment_counter(key, window_seconds=window_seconds) > limit
