# "Django AppConfig for converters."
from django.apps import AppConfig


class ConvertersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.converters"

    def ready(self):
        from .registry import registry

        registry.load_builtin_converters()
