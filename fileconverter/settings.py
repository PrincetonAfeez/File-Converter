# "Django project settings and environment config."
from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Best-effort .env loader so local `manage.py` runs honor .env without a hard dependency.

    Existing environment variables always win (values are only set as defaults).
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    value = os.environ.get(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-change-me"
    else:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set to a strong secret when DJANGO_DEBUG is disabled."
        )
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.organizations",
    "apps.files",
    "apps.converters",
    "apps.conversions",
    "apps.quotas",
    "apps.audit",
    "apps.ops.apps.OpsConfig",
]

MIDDLEWARE = [
    "fileconverter.middleware.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "fileconverter.middleware.LoginAccessMiddleware",
    "fileconverter.middleware.RowLevelSecurityMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "fileconverter.middleware.ContentSecurityPolicyMiddleware",
    "fileconverter.middleware.SlowQueryMiddleware",
]

ROOT_URLCONF = "fileconverter.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "fileconverter.wsgi.application"

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}")


def _postgres_config_from_url(url: str) -> dict:
    """Parse a postgres/postgresql DATABASE_URL into Django DATABASES settings."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": int(os.environ.get("DJANGO_DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }


if DATABASE_URL.startswith("sqlite:///"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": DATABASE_URL.removeprefix("sqlite:///"),
        }
    }
else:
    # Keep the dependency surface small: parse postgres:// DATABASE_URL directly, or fall
    # back to discrete POSTGRES_* variables when DATABASE_URL is absent or non-postgres.
    # IMPORTANT: the app DB role MUST be a non-superuser without BYPASSRLS, or the row-level
    # security tenant-isolation policies (migration conversions/0002) are silently ignored.
    # /ops/ready reports "rls": "degraded" if this is misconfigured.
    if DATABASE_URL.startswith(("postgres://", "postgresql://")):
        DATABASES = {"default": _postgres_config_from_url(DATABASE_URL)}
    else:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": os.environ.get("POSTGRES_DB", "fileconverter"),
                "USER": os.environ.get("POSTGRES_USER", "fileconverter"),
                "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "fileconverter"),
                "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
                "PORT": os.environ.get("POSTGRES_PORT", "5432"),
                "CONN_MAX_AGE": int(os.environ.get("DJANGO_DB_CONN_MAX_AGE", "60")),
                "CONN_HEALTH_CHECKS": True,
            }
        }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Session hardening: shorter lifetime than the 2-week default, refreshed on activity,
# and cleared when the browser closes.
SESSION_COOKIE_AGE = int(os.environ.get("DJANGO_SESSION_COOKIE_AGE", str(60 * 60 * 8)))
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("DJANGO_SESSION_EXPIRE_AT_BROWSER_CLOSE", True)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TZ", "America/Los_Angeles")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_ROOT = Path(os.environ.get("FILECONVERTER_STORAGE_ROOT", BASE_DIR / "media"))
if not MEDIA_ROOT.is_absolute():
    MEDIA_ROOT = BASE_DIR / MEDIA_ROOT
MEDIA_URL = "media/"

# Scratch space for in-flight conversions. Kept OUTSIDE MEDIA_ROOT so intermediate
# working files are never exposed through the media URL / object-storage key space.
FILECONVERTER_WORK_ROOT = Path(
    os.environ.get("FILECONVERTER_WORK_ROOT", BASE_DIR / "work")
)
if not FILECONVERTER_WORK_ROOT.is_absolute():
    FILECONVERTER_WORK_ROOT = BASE_DIR / FILECONVERTER_WORK_ROOT

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = int(os.environ.get("CELERY_TASK_TIME_LIMIT", "600"))
CELERY_TASK_SOFT_TIME_LIMIT = int(os.environ.get("CELERY_TASK_SOFT_TIME_LIMIT", "540"))
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ROUTES = {
    "conversions.process_conversion_job": {"queue": "default"},
    "conversions.reap_stale_jobs": {"queue": "default"},
    "conversions.expire_outputs": {"queue": "default"},
    "conversions.purge_input_files": {"queue": "default"},
    "conversions.gc_blobs": {"queue": "default"},
    "conversions.purge_quota_decisions": {"queue": "default"},
    "audit.deliver_outbox": {"queue": "default"},
    "audit.purge_outbox": {"queue": "default"},
    "ops.monitor_queue_backlog": {"queue": "default"},
}
CELERY_BEAT_SCHEDULE = {
    "reap-stale-jobs": {
        "task": "conversions.reap_stale_jobs",
        "schedule": float(os.environ.get("FILECONVERTER_REAP_INTERVAL_SECONDS", "120")),
    },
    "expire-outputs": {
        "task": "conversions.expire_outputs",
        "schedule": float(os.environ.get("FILECONVERTER_EXPIRE_INTERVAL_SECONDS", "300")),
    },
    "purge-input-files": {
        "task": "conversions.purge_input_files",
        "schedule": float(os.environ.get("FILECONVERTER_INPUT_PURGE_INTERVAL_SECONDS", "600")),
    },
    "gc-blobs": {
        "task": "conversions.gc_blobs",
        "schedule": float(os.environ.get("FILECONVERTER_BLOB_GC_INTERVAL_SECONDS", "900")),
    },
    "purge-quota-decisions": {
        "task": "conversions.purge_quota_decisions",
        "schedule": float(os.environ.get("FILECONVERTER_PURGE_INTERVAL_SECONDS", "3600")),
    },
    "deliver-outbox": {
        "task": "audit.deliver_outbox",
        "schedule": float(os.environ.get("FILECONVERTER_OUTBOX_INTERVAL_SECONDS", "60")),
    },
    "purge-outbox": {
        "task": "audit.purge_outbox",
        "schedule": float(os.environ.get("FILECONVERTER_OUTBOX_PURGE_INTERVAL_SECONDS", "3600")),
    },
    "monitor-queue-backlog": {
        "task": "ops.monitor_queue_backlog",
        "schedule": float(os.environ.get("FILECONVERTER_BACKLOG_MONITOR_INTERVAL_SECONDS", "60")),
    },
}
# Alert threshold: warn when a broker queue exceeds this many pending messages.
FILECONVERTER_QUEUE_BACKLOG_ALERT = int(os.environ.get("FILECONVERTER_QUEUE_BACKLOG_ALERT", "100"))
# Bearer token for scraping /ops/metrics/ (job counts etc.). Empty => staff session only;
# anonymous access is always denied.
FILECONVERTER_METRICS_TOKEN = os.environ.get("FILECONVERTER_METRICS_TOKEN", "")

# When DEBUG is False, require SENTRY_DSN (check --deploy + readiness). Opt out explicitly
# for ephemeral stacks that still run deploy checks (CI sets this to False).
FILECONVERTER_REQUIRE_SENTRY = env_bool("FILECONVERTER_REQUIRE_SENTRY", not DEBUG)

# Feature flags override map (deploy-level). DB rows (apps.ops.FeatureFlag) allow runtime
# toggles without a deploy. Resolution: this map -> DB row -> code default.
FILECONVERTER_FLAGS: dict[str, bool] = {}

# Maximum times a job may be (re)claimed before a transient failure becomes terminal.
FILECONVERTER_MAX_ATTEMPTS = int(os.environ.get("FILECONVERTER_MAX_ATTEMPTS", "3"))

if env_bool("FILECONVERTER_USE_REDIS_CACHE", False):
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

FILECONVERTER_MAX_UPLOAD_BYTES = int(
    float(os.environ.get("FILECONVERTER_MAX_UPLOAD_MB", "50")) * 1024 * 1024
)
# Request-rate throttle on the authenticated upload/convert endpoint (abuse control on top
# of the active-job quota). Max submissions per user per rolling window.
FILECONVERTER_UPLOAD_RATE_MAX = int(os.environ.get("FILECONVERTER_UPLOAD_RATE_MAX", "20"))
FILECONVERTER_UPLOAD_RATE_WINDOW_SECONDS = int(
    os.environ.get("FILECONVERTER_UPLOAD_RATE_WINDOW_SECONDS", "60")
)
FILECONVERTER_OUTPUT_TTL_HOURS = int(os.environ.get("FILECONVERTER_OUTPUT_TTL_HOURS", "24"))
# Retention for stored INPUT files after a job reaches a terminal state (row is retained
# for audit; the bytes are purged). Defaults to the output TTL.
FILECONVERTER_INPUT_TTL_HOURS = int(
    os.environ.get("FILECONVERTER_INPUT_TTL_HOURS", str(FILECONVERTER_OUTPUT_TTL_HOURS))
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# TLS hardening. Defaults are safe for production (DEBUG off) and relaxed for local dev.
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SECURE_HSTS_SECONDS = int(
    os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000")
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", not DEBUG)
SECURE_CONTENT_TYPE_NOSNIFF = True

# When content sniffing detects a MIME that contradicts the declared extension, reject
# the upload. Enabled by default. If REQUIRE_MIME_SCANNER is set, uploads are refused when
# libmagic is unavailable (fail closed) instead of silently skipping the check.
FILECONVERTER_ENFORCE_MIME_MATCH = env_bool("FILECONVERTER_ENFORCE_MIME_MATCH", True)
FILECONVERTER_REQUIRE_MIME_SCANNER = env_bool("FILECONVERTER_REQUIRE_MIME_SCANNER", False)

# Retention for QuotaDecision audit rows purged by the periodic maintenance task.
FILECONVERTER_QUOTA_DECISION_TTL_DAYS = int(
    os.environ.get("FILECONVERTER_QUOTA_DECISION_TTL_DAYS", "30")
)

# Neutralize cells/headers that spreadsheet apps would evaluate as formulas on CSV/XLSX
# output (CSV formula injection defense).
FILECONVERTER_SANITIZE_SPREADSHEET_FORMULAS = env_bool(
    "FILECONVERTER_SANITIZE_SPREADSHEET_FORMULAS", True
)
# Row ceiling for tabular conversions (bounds worker memory; stays under the .xlsx limit).
FILECONVERTER_MAX_TABLE_ROWS = int(os.environ.get("FILECONVERTER_MAX_TABLE_ROWS", "1000000"))

# Login brute-force throttling. Accurate enforcement requires a shared cache (Redis);
# with the default per-process cache the limit scales with the number of web processes.
FILECONVERTER_LOGIN_MAX_ATTEMPTS = int(os.environ.get("FILECONVERTER_LOGIN_MAX_ATTEMPTS", "10"))
FILECONVERTER_LOGIN_BLOCK_SECONDS = int(os.environ.get("FILECONVERTER_LOGIN_BLOCK_SECONDS", "300"))
# Per-IP aggregate cap (across all usernames) to blunt credential stuffing from one source.
FILECONVERTER_LOGIN_IP_MAX_ATTEMPTS = int(
    os.environ.get("FILECONVERTER_LOGIN_IP_MAX_ATTEMPTS", "50")
)
# Number of trusted reverse proxies in front of the app. When > 0, the real client IP is
# read from X-Forwarded-For that many hops from the right (else REMOTE_ADDR is used).
FILECONVERTER_TRUSTED_PROXY_COUNT = int(
    os.environ.get("FILECONVERTER_TRUSTED_PROXY_COUNT", "0")
)

# Outbox webhook delivery. When unset, events are recorded but not delivered (pending).
FILECONVERTER_WEBHOOK_URL = os.environ.get("FILECONVERTER_WEBHOOK_URL", "")
FILECONVERTER_WEBHOOK_TIMEOUT_SECONDS = float(
    os.environ.get("FILECONVERTER_WEBHOOK_TIMEOUT_SECONDS", "5")
)
# Shared secret for HMAC-SHA256 signing of webhook payloads (X-Signature header).
FILECONVERTER_WEBHOOK_SECRET = os.environ.get("FILECONVERTER_WEBHOOK_SECRET", "")
FILECONVERTER_OUTBOX_MAX_ATTEMPTS = int(os.environ.get("FILECONVERTER_OUTBOX_MAX_ATTEMPTS", "8"))
FILECONVERTER_OUTBOX_TTL_DAYS = int(os.environ.get("FILECONVERTER_OUTBOX_TTL_DAYS", "7"))

# Retention (hours) for FileBlob rows whose bytes were already purged / that are orphaned
# (referenced by no job). Swept by the periodic FileBlob GC task.
FILECONVERTER_BLOB_GC_TTL_HOURS = int(os.environ.get("FILECONVERTER_BLOB_GC_TTL_HOURS", "48"))

# Content-Security-Policy (defense-in-depth; template autoescaping is the primary control).
# 'unsafe-inline' for style covers the inline width:% on the progress bar; scripts are all
# served from our own origin (htmx is vendored locally).
FILECONVERTER_CSP = os.environ.get(
    "FILECONVERTER_CSP",
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
)

# Log queries slower than this many ms (0 disables). Production default: 500ms.
_SLOW_QUERY_DEFAULT = "0" if DEBUG else "500"
FILECONVERTER_SLOW_QUERY_MS = int(
    os.environ.get("FILECONVERTER_SLOW_QUERY_MS", _SLOW_QUERY_DEFAULT)
)

LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "INFO")
# Structured JSON logs for aggregation (Loki/ELK/Datadog). Text otherwise.
_LOG_FORMATTER = "json" if env_bool("FILECONVERTER_JSON_LOGS", False) else "verbose"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "fileconverter.middleware.RequestIDFilter"},
    },
    "formatters": {
        "verbose": {
            "format": (
                "%(asctime)s %(levelname)s %(name)s %(process)d "
                "req=%(request_id)s %(message)s"
            ),
        },
        "json": {"()": "fileconverter.observability.JsonLogFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": _LOG_FORMATTER,
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

# Optional error tracking (no-op unless SENTRY_DSN is set and sentry-sdk is installed).
from fileconverter.observability import init_sentry  # noqa: E402

init_sentry()
