"""
One-time carryover: seed PortalSettings.contact_email/contact_whatsapp from
the HEPHZIBAH_EMAIL / HEPHZIBAH_WHATSAPP env vars that used to back
settings.HEPHZIBAH_CONTACT, so an existing deployment doesn't silently lose
its configured contact info the moment this code ships (env vars are gone by
the time this migration runs in a fresh deploy, so read os.environ directly
rather than the no-longer-existing settings.HEPHZIBAH_CONTACT). New/empty
environments just get blank fields, exactly as PortalSettings' other fields
already behave until staff fills them in.
"""

import os

from django.db import migrations


def seed(apps, schema_editor):
    email = os.environ.get("HEPHZIBAH_EMAIL", "").strip()
    whatsapp = os.environ.get("HEPHZIBAH_WHATSAPP", "").strip()
    if not email and not whatsapp:
        return

    PortalSettings = apps.get_model("portal", "PortalSettings")
    settings_row, _ = PortalSettings.objects.get_or_create(pk=1)
    if email:
        settings_row.contact_email = email
    if whatsapp:
        settings_row.contact_whatsapp = whatsapp
    settings_row.save(update_fields=["contact_email", "contact_whatsapp"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0012_portalsettings_contact_email_and_more"),
    ]

    operations = [
        migrations.RunPython(seed, noop),
    ]
