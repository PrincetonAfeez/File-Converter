# "Management command: export user data."
from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.organizations.lifecycle import export_user_data


class Command(BaseCommand):
    help = "Export a user's account and conversion history as JSON (data-subject access)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--output", help="Write to this file instead of stdout.")

    def handle(self, *args, **options):
        user_model = get_user_model()
        try:
            user = user_model.objects.get(username=options["username"])
        except user_model.DoesNotExist as exc:
            raise CommandError(f"No user named {options['username']!r}") from exc
        payload = json.dumps(export_user_data(user), indent=2)
        if options["output"]:
            with open(options["output"], "w", encoding="utf-8") as handle:
                handle.write(payload)
            self.stdout.write(self.style.SUCCESS(f"Wrote export to {options['output']}"))
        else:
            self.stdout.write(payload)
