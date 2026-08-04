"""
Seed one ScheduledTaskSettings row per gated periodic/background Celery task,
all enabled=True — same reasoning as 0004: populate the admin list
immediately rather than leaving it empty until a task happens to be checked.
"""

from django.db import migrations

TASKS = [
    ("payment_due_digest", "Payment due digest (daily)"),
    ("meeting_prep_digest", "Meeting prep digest (daily)"),
    ("notifications_retry_failed", "Retry failed notifications (hourly)"),
    ("notifications_cleanup_old", "Cleanup old notifications (weekly)"),
    ("event_details_notification", "Event details updated email (debounced)"),
]


def seed(apps, schema_editor):
    ScheduledTaskSettings = apps.get_model("notifications", "ScheduledTaskSettings")
    for task_key, label in TASKS:
        ScheduledTaskSettings.objects.get_or_create(
            task_key=task_key, defaults={"label": label, "is_enabled": True},
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0009_scheduledtasksettings"),
    ]

    operations = [
        migrations.RunPython(seed, noop),
    ]
