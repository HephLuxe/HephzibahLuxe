"""
apps/events/tasks.py

dispatch_due_event_details_notifications — the sweep that sends the debounced
"event details updated" client email once an engagement's quiet window has
closed. Runs from cron group `notification_retry`.

This used to be a per-edit delayed task (`apply_async(countdown=900)`) that
compared its own token against the engagement's and no-opped if superseded.
Elegant, but only half durable: the token was a column, the schedule was a
message in a broker. A sweep over a `due_at` column is the same debounce with
the whole of its state in Postgres — see EventEngagement's field comments and
docs/adr/0001-remove-celery.md.

Domain-specific send logic lives here (not in apps.notifications) —
notifications only owns the generic send/retry/cleanup mechanics.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.core.background import background_task

logger = logging.getLogger(__name__)


@background_task(name="events.dispatch_due_event_details")
def dispatch_due_event_details_notifications() -> None:
    """
    Send one email per engagement whose debounce window has closed.

    Precision is quantised to the cron cadence: a 15-minute debounce swept every
    10 minutes lands 15–25 minutes after the last edit rather than exactly 15.
    For "the planner finished editing, tell the client" that is not a meaningful
    difference, and it buys a debounce that survives a deploy — which the exact
    version did not.
    """
    from apps.notifications.models import ScheduledTaskSettings
    from apps.notifications.services import queue_notification
    from apps.portal.models import EventEngagement

    if not ScheduledTaskSettings.is_task_enabled("event_details_notification"):
        # The real skip point is services.schedule_event_details_notification,
        # which won't stamp a new due_at. This is the defensive half: rows
        # stamped before the setting was toggled off must not fire either.
        return

    due = EventEngagement.objects.filter(
        event_details_notify_due_at__lte=timezone.now(),
    ).select_related("portal__user", "event", "event__celebrant")

    sent = 0
    for engagement in due:
        # Clear the schedule FIRST, in its own transaction, and read back what we
        # actually cleared. Two things fall out of that ordering:
        #
        #   * A crash between here and queue_notification loses one email rather
        #     than re-sending it on every sweep for ever.
        #   * A concurrent edit that lands between this UPDATE and the send
        #     re-stamps a later due_at, so its own change gets its own email —
        #     which is exactly the debounce semantics.
        #
        # The UPDATE is filtered on due_at still being set, so two overlapping
        # sweeps cannot both claim the same row. Platform cron won't start a run
        # while the previous one is going, but that is the platform's promise,
        # not ours.
        with transaction.atomic():
            claimed = EventEngagement.objects.filter(
                pk=engagement.pk, event_details_notify_due_at__isnull=False
            ).update(
                event_details_notify_due_at=None,
                event_details_notify_token=None,
                event_details_notify_what="",
            )
        if not claimed:
            continue

        event = engagement.event
        celebrant = event.celebrant if event else None
        if not celebrant:
            continue

        queue_notification(
            recipient_email=celebrant.email,
            recipient_user=celebrant,
            engagement=engagement,
            template_name="event_details_updated",
            context={
                "event_title": event.title,
                # Read off the in-memory instance, which still holds the value
                # the UPDATE above just blanked in the database. Deliberate: the
                # row was claimed, so this is the description that belongs to
                # this email, and a refresh_from_db here would read back "".
                "what": engagement.event_details_notify_what,
            },
        )
        sent += 1

    if sent:
        logger.info(
            "dispatched %s debounced event-details notification(s)",
            sent,
            extra={"event": "event_details_dispatched", "count": sent},
        )
