# "Tests for production settings defaults."
"""Tests for production default settings."""

from __future__ import annotations

import importlib


def test_slow_query_default_is_500_when_debug_off(monkeypatch):
    monkeypatch.setenv("DJANGO_DEBUG", "False")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "test-prod-defaults-key-0123456789")
    monkeypatch.delenv("FILECONVERTER_SLOW_QUERY_MS", raising=False)
    settings = importlib.import_module("fileconverter.settings")
    importlib.reload(settings)
    assert settings.FILECONVERTER_SLOW_QUERY_MS == 500
