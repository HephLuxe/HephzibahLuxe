"""
apps/meetings/tasks.py

meeting_prep_digest_task — daily scan (Celery beat, see config/settings.py
CELERY_BEAT_SCHEDULE) for upcoming meetings with incomplete required prep
that haven't been notified yet. Domain-specific digest logic lives here (not
in apps.notifications) — notifications only owns the generic send/retry/
cleanup mechanics; this app is the one that knows what a Meeting/prep item is.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

LOOKAHEAD_DAYS = 3


@shared_task(name="meetings.meeting_prep_digest")
def meeting_prep_digest_task() -> None:
    from apps.notifications.models import ScheduledTaskSettings
    from apps.notifications.services import queue_notification

    if not ScheduledTaskSettings.is_task_enabled("meeting_prep_digest"):
        return

    from .models import Meeting, MeetingStatus

    today = timezone.now().date()
    cutoff = today + timedelta(days=LOOKAHEAD_DAYS)

    meetings = Meeting.objects.filter(
        status__in=[MeetingStatus.UPCOMING, MeetingStatus.RESCHEDULED, MeetingStatus.ACTIVE],
        date__gte=today,
        date__lte=cutoff,
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
        meeting.prep_reminder_sent_at = timezone.now()
        meeting.save(update_fields=["prep_reminder_sent_at"])
