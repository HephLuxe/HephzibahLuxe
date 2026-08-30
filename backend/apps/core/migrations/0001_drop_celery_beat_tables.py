"""
Drop the django_celery_beat tables.

`django_celery_beat` was removed from INSTALLED_APPS with the rest of Celery
(docs/adr/0001-remove-celery.md). Removing an app does not drop its tables — they
would simply sit there for ever, six tables and 21 rows in `django_migrations`
describing a scheduler that no longer exists.

Done as a migration rather than as a documented SQL snippet in RUNBOOK.md, for
two reasons:

  * It runs itself, on the deploy that removes the code. An operator step that
    has to be remembered separately is an operator step that gets skipped.
  * It also clears `django_migrations`, so the state is genuinely consistent. If
    Celery were ever reinstated (see the ADR's rollback section), re-adding the
    app and running `migrate` recreates the tables cleanly — which is not true if
    the migration records are left behind pointing at tables that were dropped
    by hand.

`IF EXISTS` throughout, so this is a no-op on a fresh database that never had the
app installed — which is the case for every new environment and for the test
database.

WHAT IS LOST: any per-task on/off state that lived only in `PeriodicTask.enabled`.
That is not the project's kill switch — `notifications.ScheduledTaskSettings` is,
and it is untouched and still checked as the first statement of every scheduled
task. What genuinely goes is each job's *crontab*, which now lives in the cron
service schedules (see apps/core/management/commands/run_scheduled.py). The
shipped defaults are recorded there and in each cron job's schedule, so
nothing is unrecoverable.

This first migration in apps/core is deliberately schema-only for another app's
tables; apps.core defines no concrete models of its own (only abstract bases in
models.py), which is why there was no 0001 here before.
"""

from django.db import migrations

TABLES = [
    "django_celery_beat_periodictask",
    "django_celery_beat_periodictasks",
    "django_celery_beat_crontabschedule",
    "django_celery_beat_intervalschedule",
    "django_celery_beat_solarschedule",
    "django_celery_beat_clockedschedule",
]

# CASCADE because periodictask carries FKs to the four schedule tables; dropping
# them in dependency order would work too, but CASCADE cannot be got wrong by a
# later edit to the list above.
DROP_SQL = "\n".join(f'DROP TABLE IF EXISTS "{table}" CASCADE;' for table in TABLES)

FORGET_MIGRATIONS_SQL = "DELETE FROM django_migrations WHERE app = 'django_celery_beat';"


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql=DROP_SQL + "\n" + FORGET_MIGRATIONS_SQL,
            # Irreversible: recreating these tables is `django_celery_beat`'s own
            # migrations' job, not ours. Re-add the app to INSTALLED_APPS and run
            # `migrate` — that is the documented rollback.
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
