"""
apps/document_hub/tasks.py

payment_due_digest_task — daily scan (cron group `daily_maintenance`, see
apps/core/management/commands/run_scheduled.py) for PENDING milestones due
within LOOKAHEAD_DAYS that haven't been notified yet. Domain-specific digest
logic lives here (not in apps.notifications) — notifications only owns the
generic send/retry/cleanup mechanics; this app is the one that knows what a
PaymentMilestone is.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from apps.core.background import background_task
from apps.core.timezones import local_today, max_utc_offset_days

logger = logging.getLogger(__name__)

LOOKAHEAD_DAYS = 3


@background_task(name="document_hub.payment_due_digest")
def payment_due_digest_task() -> None:
    from apps.notifications.models import ScheduledTaskSettings
    from apps.notifications.services import queue_notification

    if not ScheduledTaskSettings.is_task_enabled("payment_due_digest"):
        return

    from .models import PaymentMilestone, PaymentMilestoneStatus

    # `due_date` is a naive DateField — a calendar day in the CLIENT's world, not
    # an instant — so "is it within the lookahead?" has to be answered against the
    # client's own today, which is what local_today() gives. This is a worldwide
    # platform; UTC's today is a different day from theirs for anyone far enough
    # off UTC. See apps/core/timezones.py.
    #
    # One query, then a per-recipient decision. The query is widened by the
    # maximum possible date skew, which makes it a guaranteed superset of every
    # row any recipient could consider due; the exact per-recipient check happens
    # in the loop. The alternative — a query per distinct timezone — buys nothing
    # and costs a round trip each.
    #
    # Only the upper bound is widened: there is deliberately no lower bound, so
    # an overdue milestone stays in scope until it is paid or notified.
    query_cutoff = timezone.now().date() + timedelta(
        days=LOOKAHEAD_DAYS + max_utc_offset_days()
    )

    milestones = PaymentMilestone.objects.filter(
        status=PaymentMilestoneStatus.PENDING,
        due_date__isnull=False,
        due_date__lte=query_cutoff,
        reminder_sent_at__isnull=True,
    ).select_related("schedule__engagement__portal__user", "schedule__engagement__event")

    for milestone in milestones:
        engagement = milestone.schedule.engagement
        if engagement is None:
            continue

        user = engagement.portal.user

        # The real test, in the recipient's calendar. Rows the widened query
        # pulled in but that are not yet due for THIS client are skipped, and
        # picked up on a later run once they are.
        if milestone.due_date > local_today(user) + timedelta(days=LOOKAHEAD_DAYS):
            continue
        # Marker committed FIRST, then the send. The reverse order — queue,
        # then mark — left a window where the email was already on its way and
        # `reminder_sent_at` was still NULL, so the next daily run mailed the
        # client about the same milestone again.
        #
        # Deliberately not wrapped in `transaction.atomic()`. That would look
        # tidier but it inverts the guarantee here: this task runs in a cron
        # process, where background dispatch is inline (async is opt-in and only
        # wsgi.py opts in), so the Brevo call would happen *inside* the
        # transaction — before the marker is durable. In autocommit the save
        # below is committed by the time queue_notification is reached, in both
        # process modes.
        #
        # The residual failure is a milestone marked but never emailed. That is
        # the right way round: quiet, and visible in the admin, rather than a
        # client billed twice by inbox.
        milestone.reminder_sent_at = timezone.now()
        milestone.save(update_fields=["reminder_sent_at"])
        queue_notification(
            recipient_email=user.email,
            recipient_user=user,
            engagement=engagement,
            template_name="payment_due",
            context={
                "label": milestone.label,
                "amount": str(milestone.amount),
                "due_date": str(milestone.due_date),
                "event_title": engagement.event.title if engagement.event else "",
            },
        )
