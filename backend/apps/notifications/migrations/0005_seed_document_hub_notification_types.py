"""
Seed one NotificationTypeSettings row (enabled=True) per new document_hub
notification_contexts.RENDERERS key, same pattern as 0004 — so they show up
in the admin list immediately with their own toggle, rather than only
appearing implicitly-enabled the first time one is queued.
"""

from django.db import migrations

TEMPLATE_NAMES = [
    "document_added",
    "invoice_issued",
    "receipt_issued",
    "milestone_paid",
]


def seed(apps, schema_editor):
    NotificationTypeSettings = apps.get_model("notifications", "NotificationTypeSettings")
    for name in TEMPLATE_NAMES:
        NotificationTypeSettings.objects.get_or_create(template_name=name, defaults={"enabled": True})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0004_seed_notification_types"),
    ]

    operations = [
        migrations.RunPython(seed, noop),
    ]
