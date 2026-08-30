"""
Realign ScheduledTaskSettings with the post-Celery task list.

Three changes, all data-only:

  * Adds a row for each of the four maintenance tasks that did not exist before
    (JWT flush, reset-token prune, session clear, orphaned-document sweep). They
    are fail-open, so they would run without a row — but an empty admin list is
    how a kill switch nobody knows about becomes a kill switch nobody uses.

  * Deletes `notifications_brevo_health_probe`. The active probe is gone: 288
    scheduled runs and 576 outbound HTTPS calls a day, to learn a few minutes
    earlier what the next real send would have reported. Brevo health is now
    detected passively from send outcomes, with a staleness ceiling on the
    `down` verdict (ServiceHealthState.DOWN_STALE_AFTER) so a stale row can
    never park mail indefinitely.

  * Retitles the two rows whose labels named a cadence that has changed —
    the retry sweep is */10 now, not hourly.
"""

from django.db import migrations

NEW_TASKS = [
    ("accounts_flush_expired_jwt", "Flush expired JWTs (daily)"),
    ("accounts_prune_reset_tokens", "Prune expired password-reset tokens (daily)"),
    ("core_clear_sessions", "Clear expired Django sessions (daily)"),
    ("documents_cleanup_orphaned", "Clean up orphaned documents and blobs (weekly)"),
]

RELABEL = {
    "notifications_retry_failed": "Retry failed and stranded notifications (every 10 min)",
    "event_details_notification": "Event details updated email (debounced sweep)",
}

OLD_LABELS = {
    "notifications_retry_failed": "Retry failed notifications (hourly)",
    "event_details_notification": "Event details updated email (debounced)",
}

REMOVED = ["notifications_brevo_health_probe"]


def forwards(apps, schema_editor):
    ScheduledTaskSettings = apps.get_model("notifications", "ScheduledTaskSettings")

    for task_key, label in NEW_TASKS:
        ScheduledTaskSettings.objects.get_or_create(
            task_key=task_key, defaults={"label": label, "is_enabled": True},
        )

    for task_key, label in RELABEL.items():
        ScheduledTaskSettings.objects.filter(task_key=task_key).update(label=label)

    ScheduledTaskSettings.objects.filter(task_key__in=REMOVED).delete()


def backwards(apps, schema_editor):
    ScheduledTaskSettings = apps.get_model("notifications", "ScheduledTaskSettings")

    ScheduledTaskSettings.objects.filter(
        task_key__in=[key for key, _ in NEW_TASKS]
    ).delete()

    for task_key, label in OLD_LABELS.items():
        ScheduledTaskSettings.objects.filter(task_key=task_key).update(label=label)

    ScheduledTaskSettings.objects.get_or_create(
        task_key="notifications_brevo_health_probe",
        defaults={"label": "Brevo health probe (every 5 min)", "is_enabled": True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0015_seed_inquiry_notification_types"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
