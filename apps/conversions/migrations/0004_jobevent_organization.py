# "Database migration 0004: jobevent organization."
import django.db.models.deletion
from django.db import migrations, models


def backfill_jobevent_organization(apps, schema_editor):
    JobEvent = apps.get_model("conversions", "JobEvent")
    for event in JobEvent.objects.select_related("job").iterator():
        if event.organization_id is None and event.job_id:
            JobEvent.objects.filter(pk=event.pk).update(organization_id=event.job.organization_id)


class Migration(migrations.Migration):
    dependencies = [
        ("conversions", "0003_extend_row_level_security"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobevent",
            name="organization",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="job_events",
                to="organizations.organization",
            ),
        ),
        migrations.RunPython(backfill_jobevent_organization, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="jobevent",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="job_events",
                to="organizations.organization",
            ),
        ),
    ]
