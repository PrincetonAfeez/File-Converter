# "WSGI application entrypoint with Sentry init."
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fileconverter.settings")

from fileconverter.observability import init_sentry  # noqa: E402

init_sentry()

application = get_wsgi_application()
