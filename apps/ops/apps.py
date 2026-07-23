# "Django AppConfig registering ops system checks."
from django.apps import AppConfig


class OpsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ops"

    def ready(self) -> None:
        from . import checks  # noqa: F401
