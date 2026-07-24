# "Tests for Sentry and logging helpers."
"""Tests for observability helpers."""

from __future__ import annotations

from unittest.mock import patch

from fileconverter.observability import init_sentry, sentry_initialized


def test_init_sentry_returns_false_without_dsn():
    with patch.dict("os.environ", {}, clear=True):
        assert init_sentry() is False
        assert sentry_initialized() is False


def test_init_sentry_initializes_when_dsn_set():
    with patch.dict(
        "os.environ",
        {"SENTRY_DSN": "https://example@sentry.io/1", "SENTRY_ENVIRONMENT": "test"},
        clear=True,
    ):
        with patch("sentry_sdk.init") as init:
            assert init_sentry() is True
            init.assert_called_once()
            assert sentry_initialized() is True
