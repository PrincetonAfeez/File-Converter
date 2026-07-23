# "Database migration 0003: outboxevent failed at."
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_outboxevent_attempts_outboxevent_last_attempt_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboxevent",
            name="failed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="outboxevent",
            index=models.Index(fields=["failed_at"], name="audit_outbo_failed__a1b2c3_idx"),
        ),
    ]
