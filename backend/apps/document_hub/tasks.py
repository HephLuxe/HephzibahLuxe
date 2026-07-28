"""
apps/document_hub/tasks.py

payment_due_digest_task — daily scan (Celery beat, see config/settings.py
CELERY_BEAT_SCHEDULE) for PENDING milestones due within LOOKAHEAD_DAYS that
haven't been notified yet. Domain-specific digest logic lives here (not in
apps.notifications) — notifications only owns the generic send/retry/cleanup
mechanics; this app is the one that knows what a PaymentMilestone is.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

LOOKAHEAD_DAYS = 3


@shared_task(name="document_hub.payment_due_digest")
def payment_due_digest_task() -> None:
    from apps.notifications.models import ScheduledTaskSettings
    from apps.notifications.services import queue_notification

    if not ScheduledTaskSettings.is_task_enabled("payment_due_digest"):
        return

    from .models import PaymentMilestone, PaymentMilestoneStatus

    today = timezone.now().date()
    cutoff = today + timedelta(days=LOOKAHEAD_DAYS)

    milestones = PaymentMilestone.objects.filter(
        status=PaymentMilestoneStatus.PENDING,
        due_date__isnull=False,
        due_date__lte=cutoff,
        reminder_sent_at__isnull=True,
    ).select_related("schedule__engagement__portal__user", "schedule__engagement__event")

    for milestone in milestones:
        engagement = milestone.schedule.engagement
        if engagement is None:
            continue

        user = engagement.portal.user
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
        milestone.reminder_sent_at = timezone.now()
        milestone.save(update_fields=["reminder_sent_at"])
