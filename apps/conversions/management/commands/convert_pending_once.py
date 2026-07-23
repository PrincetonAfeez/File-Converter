# "Management command: convert pending once."
from django.core.management.base import BaseCommand

from apps.conversions.models import ConversionJob
from apps.conversions.services import run_conversion


class Command(BaseCommand):
    help = "Run pending conversion jobs once without starting a Celery worker."

    def handle(self, *args, **options):
        job_ids = list(
            ConversionJob.objects.filter(
                status__in=[ConversionJob.Status.PENDING, ConversionJob.Status.RETRYING]
            ).values_list("pk", flat=True)
        )
        for job_id in job_ids:
            run_conversion(job_id)
        self.stdout.write(self.style.SUCCESS(f"Processed {len(job_ids)} pending job(s)."))
