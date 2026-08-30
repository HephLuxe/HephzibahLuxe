"""
Add the DEFERRED notification status, and relabel the rows that already meant it.

A notification parked by the Brevo circuit breaker was recorded as
`status=FAILED`, `error_message="Deferred: Brevo is currently unavailable."`,
`attempt_count=0`. The behaviour was right — don't throw mail at a dead API, and
don't spend an attempt on an outage — but the label told anyone reading the admin
that a send had been attempted and had failed, when nothing had been attempted.

DEFERRED is re-driven by the retry sweep exactly as FAILED is
(`tasks.RETRYABLE_STATUSES`), so this changes no behaviour at all. It changes what
staff see.

The data step moves existing rows across, matched on the exact marker the old
breaker path wrote. `attempt_count=0` is part of the match on purpose: a row that
carries that message *and* has spent an attempt is not one the breaker parked, and
guessing about it would be worse than leaving it alone.
"""

from django.db import migrations, models

DEFERRED_MARKER = "Deferred: Brevo is currently unavailable."


def relabel_deferred(apps, schema_editor):
    Notification = apps.get_model("notifications", "Notification")
    moved = Notification.objects.filter(
        status="failed", error_message=DEFERRED_MARKER, attempt_count=0,
    ).update(status="deferred")
    if moved:
        print(f"  relabelled {moved} parked notification(s) failed -> deferred")


def relabel_back(apps, schema_editor):
    Notification = apps.get_model("notifications", "Notification")
    Notification.objects.filter(status="deferred").update(status="failed")


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0017_scrub_auth_notification_context'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='status',
            field=models.CharField(choices=[('queued', 'Queued'), ('sent', 'Sent'), ('failed', 'Failed'), ('deferred', 'Deferred (service down)'), ('abandoned', 'Abandoned')], default='queued', max_length=20),
        ),
        migrations.RunPython(relabel_deferred, relabel_back),
    ]
