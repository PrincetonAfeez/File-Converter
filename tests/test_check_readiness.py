# "Tests for readiness probe helpers."
"""Tests for ops uptime probe helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from apps.ops.probes import probe_endpoints, ready_ok


def test_ready_ok_rejects_error_checks():
    ok, reason = ready_ok({"status": "degraded", "checks": {"database": "error"}})
    assert ok is False
    assert "database" in reason


def test_ready_ok_allows_listed_degraded_checks():
    payload = {"status": "degraded", "checks": {"database": "ok", "rls": "degraded"}}
    ok, _reason = ready_ok(payload, allow_degraded={"rls"})
    assert ok is True


def test_ready_ok_rejects_unlisted_degraded():
    payload = {"status": "degraded", "checks": {"sentry": "degraded"}}
    ok, reason = ready_ok(payload, allow_degraded={"rls"})
    assert ok is False
    assert "sentry" in reason


def test_probe_endpoints_health_only_success():
    health_body = b'{"status": "ok"}'
    response = MagicMock()
    response.status = 200
    response.read.return_value = health_body
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    with patch("apps.ops.probes.urllib.request.urlopen", return_value=response):
        ok, message = probe_endpoints("http://localhost:8000", health_only=True)
    assert ok is True
    assert "health ok" in message
