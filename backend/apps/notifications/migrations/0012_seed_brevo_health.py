"""
Seed the Brevo health-monitoring rows:

  * a ScheduledTaskSettings row for the active probe (admin on/off switch —
    same reasoning as 0010: populate the admin list immediately), and
  * the ServiceHealthState row for "brevo", so the select_for_update() in the
    recorders always locks an existing row (race-safe) and the admin shows
    Brevo's status from day one.
"""

from django.db import migrations


def seed(apps, schema_editor):
    ScheduledTaskSettings = apps.get_model("notifications", "ScheduledTaskSettings")
    ScheduledTaskSettings.objects.get_or_create(
        task_key="notifications_brevo_health_probe",
        defaults={"label": "Brevo health probe (every 5 min)", "is_enabled": True},
    )

    ServiceHealthState = apps.get_model("notifications", "ServiceHealthState")
    ServiceHealthState.objects.get_or_create(
        service="brevo", defaults={"status": "unknown"},
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0011_servicehealthstate"),
    ]

    operations = [
        migrations.RunPython(seed, noop),
    ]
