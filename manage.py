#!/usr/bin/env python
# "Django management entrypoint for local commands."
"""Django command-line utility for the file converter project."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fileconverter.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
