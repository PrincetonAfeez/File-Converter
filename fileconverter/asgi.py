# "ASGI application entrypoint."
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fileconverter.settings")

application = get_asgi_application()
