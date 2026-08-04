"""
Seed one NotificationTypeSettings row per current notification_contexts.RENDERERS
key, all enabled=True — so the admin list is populated immediately (rather than
empty until a type happens to be looked up), matching pre-existing behaviour
where every type was implicitly on. Reversible to a no-op (rows with no other
significance are safe to leave if reversed).
"""

from django.db import migrations

TEMPLATE_NAMES = [
    "new_reminder",
    "payment_due",
    "meeting_prep_due",
    "phase_advanced",
    "event_details_updated",
]


def seed(apps, schema_editor):
    NotificationTypeSettings = apps.get_model("notifications", "NotificationTypeSettings")
    for name in TEMPLATE_NAMES:
        NotificationTypeSettings.objects.get_or_create(template_name=name, defaults={"enabled": True})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0003_notificationtypesettings_delete_notificationsettings"),
    ]

    operations = [
        migrations.RunPython(seed, noop),
    ]
