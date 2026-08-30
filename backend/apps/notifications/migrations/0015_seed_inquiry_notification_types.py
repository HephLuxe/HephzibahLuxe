"""
Seed NotificationTypeSettings rows (enabled=True) for the two inquiry
templates (inquiry_received, inquiry_submitted_internal) so the on/off
toggles are visible in the admin — same pattern as 0004/0005/0008.
"""

from django.db import migrations

TEMPLATE_NAMES = [
    "inquiry_received",
    "inquiry_submitted_internal",
]


def seed(apps, schema_editor):
    NotificationTypeSettings = apps.get_model("notifications", "NotificationTypeSettings")
    for name in TEMPLATE_NAMES:
        NotificationTypeSettings.objects.get_or_create(template_name=name, defaults={"enabled": True})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0014_alter_notificationtypesettings_template_name"),
    ]

    operations = [
        migrations.RunPython(seed, noop),
    ]
