"""
Seed NotificationTypeSettings rows (enabled=True) for the two accounts-app
templates (user_credentials, password_reset) now that they're routed through
queue_notification instead of their own direct-SMTP Celery tasks — same
pattern as 0004/0005.
"""

from django.db import migrations

TEMPLATE_NAMES = [
    "user_credentials",
    "password_reset",
]


def seed(apps, schema_editor):
    NotificationTypeSettings = apps.get_model("notifications", "NotificationTypeSettings")
    for name in TEMPLATE_NAMES:
        NotificationTypeSettings.objects.get_or_create(template_name=name, defaults={"enabled": True})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0007_alter_notificationtypesettings_template_name"),
    ]

    operations = [
        migrations.RunPython(seed, noop),
    ]
