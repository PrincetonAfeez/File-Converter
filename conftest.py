# "Pytest bootstrap defaults before Django settings load."
"""Pytest bootstrap: set safe defaults before Django settings are imported.

Runs at collection time (before pytest-django calls ``django.setup()``), so the
production-safe ``DEBUG``/``SECRET_KEY`` guard in settings does not trip the suite.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ["DJANGO_DEBUG"] = "True"
os.environ["DJANGO_SECURE_SSL_REDIRECT"] = "False"
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "True")

# Default the suite to SQLite so local runs do not require PostgreSQL. Set
# FILECONVERTER_TEST_POSTGRES=1 (as verify_postgres.ps1 does) for RLS coverage.
if os.environ.get("FILECONVERTER_TEST_POSTGRES") != "1":
    test_db = Path(__file__).resolve().parent / "test_db.sqlite3"
    os.environ["DATABASE_URL"] = f"sqlite:///{test_db.as_posix()}"
