"""
One-off backfill for the plaintext-secret leak.

`Notification.context` is the exact params dict handed to Brevo, and two
templates put a live credential in it:

  * `password_reset`    -> {"code": "418302", ...}
  * `user_credentials`  -> {"temporary_password": "Xk9m2Qp...", ...}

Retention made that worse than it sounds. The weekly cleanup deleted only
`status=SENT` rows older than 90 days, so a successful credentials email left a
plaintext password in Postgres for 90 days — and a failed or abandoned one left
it there forever. Anyone with database access, a Postgres backup, or the Django
admin (which had no `context` exclusion) could read them in the clear.

services.send_now now redacts `context` when a row reaches a terminal state, and
NotificationAdmin excludes the field. This scrubs what is already stored.

Deliberately covers SENT and ABANDONED only. A QUEUED or FAILED row is still
live — the retry sweep re-reads `context` to re-send it — and scrubbing those
would silently mail a client a credentials email with no credential in it. Those
rows are scrubbed when they reach a terminal state, or deleted by the give-up
window inside a week.
"""

from django.db import migrations

AUTH_SECRET_TEMPLATES = ("password_reset", "user_credentials")
TERMINAL = ("sent", "abandoned")
REDACTED = {"redacted": True}


def forwards(apps, schema_editor):
    Notification = apps.get_model("notifications", "Notification")
    Notification.objects.filter(
        template_name__in=AUTH_SECRET_TEMPLATES, status__in=TERMINAL,
    ).exclude(context=REDACTED).update(context=REDACTED)


def backwards(apps, schema_editor):
    """Irreversible by design — the plaintext is not recoverable, which is the point."""


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0016_scheduled_task_keys_for_cron"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
