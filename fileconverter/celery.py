# "Celery app bootstrap with Sentry init."
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fileconverter.settings")

from fileconverter.observability import init_sentry  # noqa: E402

init_sentry()

app = Celery("fileconverter")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
