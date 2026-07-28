"""
apps/events/tasks.py

send_event_details_updated_notification_task — the debounced "event details
updated" client email, scheduled by services.schedule_event_details_notification
with a countdown (portal.PortalSettings.event_details_notify_debounce_seconds,
admin-configurable). Domain-specific send logic lives here (not in
apps.notifications) — notifications only owns the generic send/retry/cleanup
mechanics.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="events.send_event_details_updated_notification")
def send_event_details_updated_notification_task(engagement_id: str, token: str, what: str) -> None:
    """
    Fires after the debounce window. If `engagement.event_details_notify_token`
    no longer equals `token`, a later edit has already re-stamped it and
    scheduled its own (later) task to send instead — this one is stale, no-op.
    Otherwise this was the last edit in the quiet window: send, then clear the
    token so a duplicate/retry of this same task can't send twice.
    """
    from apps.notifications.models import ScheduledTaskSettings
    from apps.notifications.services import queue_notification
    from apps.portal.models import EventEngagement

    if not ScheduledTaskSettings.is_task_enabled("event_details_notification"):
        # Defensive: this task can already be scheduled (countdown pending)
        # from before the setting was toggled off — the real skip point is
        # services.schedule_event_details_notification, which won't schedule
        # a new one, but an already-in-flight one still needs to no-op here.
        return

    try:
        engagement = EventEngagement.objects.select_related("portal__user", "event").get(id=engagement_id)
    except EventEngagement.DoesNotExist:
        logger.info("Engagement %s no longer exists — dropping debounced notification.", engagement_id)
        return

    if str(engagement.event_details_notify_token) != token:
        return  # superseded by a later edit; that edit's own task will send

    event = engagement.event
    celebrant = event.celebrant if event else None
    if celebrant:
        queue_notification(
            recipient_email=celebrant.email,
            recipient_user=celebrant,
            engagement=engagement,
            template_name="event_details_updated",
            context={"event_title": event.title, "what": what},
        )

    engagement.event_details_notify_token = None
    engagement.save(update_fields=["event_details_notify_token"])
