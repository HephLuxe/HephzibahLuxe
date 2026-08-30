"""
apps/meetings/tasks.py

meeting_prep_digest_task — daily scan (cron group `daily_maintenance`, see
apps/core/management/commands/run_scheduled.py) for upcoming meetings with
incomplete required prep that haven't been notified yet. Domain-specific digest
logic lives here (not in apps.notifications) — notifications only owns the
generic send/retry/cleanup mechanics; this app is the one that knows what a
Meeting/prep item is.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from apps.core.background import background_task
from apps.core.timezones import local_today, max_utc_offset_days

logger = logging.getLogger(__name__)

LOOKAHEAD_DAYS = 3


@background_task(name="meetings.meeting_prep_digest")
def meeting_prep_digest_task() -> None:
    from apps.notifications.models import ScheduledTaskSettings
    from apps.notifications.services import queue_notification

    if not ScheduledTaskSettings.is_task_enabled("meeting_prep_digest"):
        return

    from .models import Meeting, MeetingStatus

    # `Meeting.date` is a naive DateField — the day of the meeting where the
    # client is — so the lookahead window has to be computed in the client's
    # calendar, not UTC's. Same reasoning and same shape as the payment-due
    # digest; the reasoning lives in apps/core/timezones.py.
    #
    # BOTH bounds are widened here (unlike the payment digest, which has no lower
    # bound): a meeting must not be nudged after it has happened, so `date__gte`
    # is load-bearing and the widened query has to reach one day back for a client
    # whose today is still yesterday in UTC.
    utc_today = timezone.now().date()
    skew = timedelta(days=max_utc_offset_days())

    meetings = Meeting.objects.filter(
        status__in=[MeetingStatus.UPCOMING, MeetingStatus.RESCHEDULED, MeetingStatus.ACTIVE],
        date__gte=utc_today - skew,
        date__lte=utc_today + timedelta(days=LOOKAHEAD_DAYS) + skew,
        preparation_required=True,
        prep_reminder_sent_at__isnull=True,
    ).select_related("engagement__portal__user")

    for meeting in meetings:
        if meeting.engagement is None:
            continue

        incomplete_count = meeting.prep_items.filter(is_completed=False).count()
        if incomplete_count == 0:
            continue

        user = meeting.engagement.portal.user

        # The real window, in the recipient's calendar. A meeting already in their
        # past gets no nudge; one beyond their lookahead waits for a later run.
        today = local_today(user)
        if not (today <= meeting.date <= today + timedelta(days=LOOKAHEAD_DAYS)):
            continue
        # Marker committed first, then the send — and deliberately no
        # `transaction.atomic()` around the pair. Same reasoning, spelled out in
        # full in apps/document_hub/tasks.py: in a cron process the send runs
        # inline, so wrapping both would put the Brevo call inside the
        # transaction and defeat the ordering this fixes.
        meeting.prep_reminder_sent_at = timezone.now()
        meeting.save(update_fields=["prep_reminder_sent_at"])
        queue_notification(
            recipient_email=user.email,
            recipient_user=user,
            engagement=meeting.engagement,
            template_name="meeting_prep_due",
            context={
                "meeting_title": meeting.title,
                "meeting_date": str(meeting.date),
                "incomplete_count": incomplete_count,
            },
        )
