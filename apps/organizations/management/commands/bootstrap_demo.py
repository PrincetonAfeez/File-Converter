# "Management command: bootstrap demo."
from django.core.management.base import BaseCommand

from apps.organizations.services import bootstrap_demo_user


class Command(BaseCommand):
    help = "Create a demo user and personal workspace for local development."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="demo")
        parser.add_argument("--password", default="demo12345")

    def handle(self, *args, **options):
        user = bootstrap_demo_user(options["username"], options["password"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Ready: {user.username} can sign in with password {options['password']}"
            )
        )
