"""
seed_periodic_tasks — install the default Celery Beat schedule into the DB.

Since the schedule now lives in django-celery-beat's DatabaseScheduler (not a
static CELERY_BEAT_SCHEDULE), this command is the source of the shipped default
timings. Run it once per environment after `migrate` — it is idempotent and
safe on every deploy:

    python manage.py seed_periodic_tasks

It creates each job only if a PeriodicTask of that name doesn't already exist,
so a timing you've since edited in the admin is left untouched. Pass --reset to
force every job back to the shipped default crontab (useful after experimenting).

Mirrors the four jobs that used to live in the static CELERY_BEAT_SCHEDULE, plus
the Brevo health probe (apps/notifications/tasks.brevo_health_probe_task).
"""

from django.core.management.base import BaseCommand

# Each entry: unique PeriodicTask name -> (registered task name, crontab kwargs).
# crontab kwargs are django_celery_beat.models.CrontabSchedule fields; all times
# are UTC to match the project's CELERY_TIMEZONE.
SCHEDULE = {
    "notifications-retry-failed": (
        "notifications.retry_failed",
        {"minute": "0"},  # hourly, on the hour
    ),
    "notifications-cleanup-old": (
        "notifications.cleanup_old",
        {"minute": "0", "hour": "3", "day_of_week": "1"},  # weekly, Monday 03:00
    ),
    "document-hub-payment-due-digest": (
        "document_hub.payment_due_digest",
        {"minute": "0", "hour": "8"},  # daily 08:00
    ),
    "meetings-prep-due-digest": (
        "meetings.meeting_prep_digest",
        {"minute": "15", "hour": "8"},  # daily 08:15
    ),
    "notifications-brevo-health-probe": (
        "notifications.brevo_health_probe",
        {"minute": "*/5"},  # every 5 minutes — catch a Brevo outage before a send
    ),
}


class Command(BaseCommand):
    help = "Install the default Celery Beat schedule (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Force every job back to its shipped default crontab.",
        )

    def handle(self, *args, **options):
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        reset = options["reset"]
        created, updated, skipped = 0, 0, 0

        for name, (task, cron_kwargs) in SCHEDULE.items():
            schedule, _ = CrontabSchedule.objects.get_or_create(
                timezone="UTC", **cron_kwargs
            )
            existing = PeriodicTask.objects.filter(name=name).first()

            if existing is None:
                PeriodicTask.objects.create(name=name, task=task, crontab=schedule)
                created += 1
                self.stdout.write(self.style.SUCCESS(f"created  {name} -> {task}"))
            elif reset:
                existing.task = task
                existing.crontab = schedule
                existing.interval = None
                existing.save(update_fields=["task", "crontab", "interval"])
                updated += 1
                self.stdout.write(self.style.WARNING(f"reset    {name} -> {task}"))
            else:
                skipped += 1
                self.stdout.write(f"skipped  {name} (already exists)")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. created={created} updated={updated} skipped={skipped}"
            )
        )
