# "Management command: delete account."
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.organizations.lifecycle import delete_user_account


class Command(BaseCommand):
    help = "Delete a user account and any organization they solely own (data-subject erasure)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--yes", action="store_true", help="Confirm irreversible deletion."
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=options["username"])
        except user_model.DoesNotExist as exc:
            raise CommandError(f"No user named {options['username']!r}") from exc
        if not options["yes"]:
            raise CommandError("Refusing to delete without --yes (irreversible).")
        delete_user_account(user)
        self.stdout.write(self.style.SUCCESS(f"Deleted account {options['username']}."))
