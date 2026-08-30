"""
Run a group of scheduled tasks in one process, then exit.

This is the only scheduler. There is no Celery beat and no worker: the platform's
cron service invokes this command, it runs one group to completion in its own
process, and exits. See docs/adr/0001-remove-celery.md.

Beat was removed because django-celery-beat's DatabaseScheduler polls
`django_celery_beat_periodictasks` every ~5 seconds on a persistent connection.
That is the right trade against an always-on database and the wrong one against a
serverless Postgres that bills for compute and autosuspends when idle — the poll
alone was enough to keep it awake, which RUNBOOK.md documented and worked around
by telling the operator to scale the beat service to zero by hand.

Groups are collapsed by cadence, so the deployment needs three cron entries
rather than nine:

    notification_retry   re-drive lost mail and send due debounced emails.
                         Wants a SHORT cadence — see the note on the group.
    daily_maintenance    the digests plus daily housekeeping
    weekly_maintenance   the two weekly cleanups

Everything runs synchronously via `.apply()`. Tasks that fan out with `.delay()`
— `notifications.retry_failed` re-dispatches each send — also run inline here,
because async dispatch is opt-in and only the web process opts in
(apps/core/background). A cron process therefore sends its own mail before
exiting rather than handing it to a pool that dies with the command.

Every task keeps its `ScheduledTaskSettings.is_task_enabled(...)` gate as its
first statement, so the admin kill switch works exactly as it did under beat.
What moved out of the admin is *timing*: that is now each cron service's
schedule.

Usage:
    python manage.py run_scheduled notification_retry
    python manage.py run_scheduled --list
"""

import logging
import time
from importlib import import_module

from django.core.management.base import BaseCommand, CommandError

# group -> ordered list of (label, "module path:task attribute").
#
# Order matters within a group.
GROUPS = {
    # Its own group and its own cron entry, at */10, because it wants a far
    # shorter cadence than anything else here — and that cadence is load-bearing,
    # not a preference.
    #
    # Since in-task retry was removed, this sweep is the ONLY retry path for a
    # failed send (send_now increments attempt_count per call, so an in-thread
    # loop would burn the whole budget inside one dispatch and the sweep would
    # then skip the row forever). A password-reset code is valid for 30 minutes
    # (accounts.utils.RESET_CODE_TTL_MINUTES), so at the old hourly cadence a
    # single transient Brevo blip would deliver a code that was already dead.
    # */10 leaves room for two attempts inside the window.
    #
    # Widening this to */15 or */30 to let a serverless Postgres suspend more
    # often is a real trade and a defensible one — but it is a trade against
    # password-reset recovery, so make it deliberately.
    "notification_retry": [
        # Stranded and failed mail first: it is the thing with a deadline.
        ("retry_failed_notifications", "apps.notifications.tasks:retry_failed_notifications_task"),
        # Then the debounced event-details emails whose quiet window has closed.
        # After the sweep, so a Brevo recovery detected above is already in
        # effect by the time these are queued.
        ("dispatch_due_event_details", "apps.events.tasks:dispatch_due_event_details_notifications"),
    ],
    # Cron at 08:00 UTC. The two digests used to be separate beat jobs 15 minutes
    # apart; the stagger only existed so two beat jobs wouldn't fire at once, and
    # running them sequentially in one process is strictly simpler.
    "daily_maintenance": [
        ("payment_due_digest", "apps.document_hub.tasks:payment_due_digest_task"),
        ("meeting_prep_digest", "apps.meetings.tasks:meeting_prep_digest_task"),
        # Housekeeping after the client-facing work: none of it is urgent, and a
        # failure here must not delay a digest.
        ("prune_reset_tokens", "apps.accounts.tasks:prune_expired_reset_tokens"),
        ("flush_expired_jwt", "apps.accounts.tasks:flush_expired_jwt_tokens"),
        ("clear_sessions", "apps.core.tasks:clear_expired_sessions"),
    ],
    # Cron at 03:00 UTC on Mondays.
    "weekly_maintenance": [
        ("cleanup_old_notifications", "apps.notifications.tasks:cleanup_old_notifications_task"),
        ("cleanup_orphaned_documents", "apps.documents.tasks:cleanup_orphaned_documents_task"),
    ],
}


def _resolve(path):
    """Turn "package.module:attribute" into the task object it names."""
    module_path, _, attribute = path.partition(":")
    return getattr(import_module(module_path), attribute)


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run one group of scheduled tasks to completion. Invoked by platform cron."

    def add_arguments(self, parser):
        parser.add_argument(
            "group",
            nargs="?",
            choices=sorted(GROUPS),
            help="Which group of tasks to run.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="Print the groups and the tasks in each, then exit without running anything.",
        )

    def handle(self, *args, **options):
        if options["list"]:
            for group, tasks in GROUPS.items():
                self.stdout.write(self.style.SUCCESS(group))
                for label, path in tasks:
                    self.stdout.write(f"  - {label}  ({path})")
            return

        group = options["group"]
        if not group:
            raise CommandError(
                "Name a group to run, or pass --list to see them. "
                f"Available: {', '.join(sorted(GROUPS))}."
            )

        self.stdout.write(f"Running group '{group}'...")
        started = time.monotonic()
        failures = []

        for label, path in GROUPS[group]:
            # .apply() runs the task in this process and returns a TaskResult
            # rather than raising, so one failing task does not strand the rest
            # of the group — failures are collected and reported at the end.
            result = _resolve(path).apply()

            if result.successful():
                self.stdout.write(self.style.SUCCESS(f"  + ok       {label}"))
            else:
                failures.append((label, result.result))
                self.stdout.write(self.style.ERROR(f"  ! failed   {label}: {result.result!r}"))

        elapsed = round(time.monotonic() - started, 3)

        if failures:
            # Emitted BEFORE raising, so the failure reaches Loki as well as the
            # platform's run history. The exit code tells Render the run failed;
            # this tells you which task and why without opening the console.
            logger.error(
                "Scheduled group '%s': %s of %s tasks failed.",
                group, len(failures), len(GROUPS[group]),
                extra={
                    "event": "scheduled_group_failed",
                    "group": group,
                    "failed": [label for label, _ in failures],
                    "task_count": len(GROUPS[group]),
                    "duration_seconds": elapsed,
                },
            )
            # Non-zero exit so the platform's own cron run history marks the run
            # failed and its alerting fires too.
            raise CommandError(
                f"{len(failures)} of {len(GROUPS[group])} tasks failed: "
                f"{', '.join(label for label, _ in failures)}"
            )

        # The HEARTBEAT, and the reason this logs at all. A failed run is already
        # visible twice over (non-zero exit + the platform's run history), but a
        # cron service that stops running ENTIRELY — paused, deleted, schedule
        # mistyped — produces no failure to notice. Nothing was wrong; nothing
        # happened. `notification_retry` is the only retry path for a failed
        # email and the only thing that sends debounced event-details mail, so
        # its silence is expensive and invisible.
        #
        # One INFO line per successful run turns that into an *absence*, which
        # Grafana can alert on:
        #     absent_over_time({service="hephzibah-api"} | json
        #                      | event="scheduled_group_completed"
        #                      | group="notification_retry" [30m])
        # See docs/observability/.
        logger.info(
            "Scheduled group '%s' completed: %s task(s) in %ss.",
            group, len(GROUPS[group]), elapsed,
            extra={
                "event": "scheduled_group_completed",
                "group": group,
                "task_count": len(GROUPS[group]),
                "duration_seconds": elapsed,
            },
        )
        self.stdout.write(self.style.SUCCESS(f"\nDone. {len(GROUPS[group])} tasks ran clean."))
