# "Tests for rate limit helpers."
"""Tests for fileconverter.ratelimit."""

from __future__ import annotations

from django.core.cache import cache

from fileconverter.ratelimit import (
    counter_value,
    increment_counter,
    rate_limit_commit,
    rate_limit_exceeded,
    rate_limit_would_exceed,
)


def test_increment_counter_starts_at_one():
    cache.clear()
    assert increment_counter("rl:test-a", window_seconds=60) == 1
    assert increment_counter("rl:test-a", window_seconds=60) == 2


def test_counter_value_defaults_to_zero():
    cache.clear()
    assert counter_value("rl:missing") == 0


def test_rate_limit_would_exceed_peek_without_commit():
    cache.clear()
    key = "rl:peek"
    assert rate_limit_would_exceed(key, limit=2) is False
    rate_limit_commit(key, window_seconds=60)
    assert rate_limit_would_exceed(key, limit=2) is False
    rate_limit_commit(key, window_seconds=60)
    assert rate_limit_would_exceed(key, limit=2) is True


def test_rate_limit_would_exceed_disabled_when_limit_zero():
    cache.clear()
    rate_limit_commit("rl:zero", window_seconds=60)
    assert rate_limit_would_exceed("rl:zero", limit=0) is False


def test_rate_limit_exceeded_legacy_helper():
    cache.clear()
    assert rate_limit_exceeded("rl:legacy", limit=2, window_seconds=60) is False
    assert rate_limit_exceeded("rl:legacy", limit=2, window_seconds=60) is False
    assert rate_limit_exceeded("rl:legacy", limit=2, window_seconds=60) is True
