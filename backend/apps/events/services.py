# apps/events/services.py

import uuid
from datetime import timedelta

from django.utils import timezone


def schedule_event_details_notification(event, what: str) -> None:
    """
    Debounced "event details updated" client email. Every edit to this event OR
    any of its EventDays calls this — rather than emailing immediately per save,
    it stamps a due time on the engagement
    (portal.PortalSettings.event_details_notify_debounce_seconds, admin-
    configurable, default 15 min). If another edit lands before that time
    arrives, this runs again and pushes the due time further out, so a burst of
    edits collapses into exactly one email describing the most recent change,
    sent once the editor has been quiet for the full window — never a fixed
    deadline the editor has to beat.

    The send itself is done by apps.events.tasks
    .dispatch_due_event_details_notifications, a sweep over `due_at` run from
    cron. Nothing is *scheduled* anywhere but this row: the previous version
    paired the token with `apply_async(countdown=...)`, which put half the
    debounce state in a broker message and lost the email outright if a worker
    restarted or a deploy landed inside the window.

    On top of that row — never instead of it — an in-process timer is armed for
    the same moment, so the email lands within seconds of the window closing
    rather than at the next cron run (up to 10 minutes later). It is pure
    opportunism: it is only armed in the web process, it is dropped by any
    restart, and it changes nothing about when the work becomes *due*. Both
    runners are safe to race because the sweep claims each row with a
    conditional UPDATE before sending, so whichever arrives first wins and the
    other finds nothing.

    Re-arming replaces the previous timer for this engagement rather than adding
    one, and a timer that fires early because a later edit pushed `due_at` out
    simply matches no rows — the debounce reset lives entirely in the column, so
    the timer needs no cancellation logic or token comparison of its own.

    Falls back to sending immediately if the event has no engagement yet (no
    row to key the debounce on — rare: engagements are created alongside events
    in the normal flow) or no celebrant to notify.
    """
    from apps.notifications.models import ScheduledTaskSettings
    from apps.portal.models import PortalSettings

    if not ScheduledTaskSettings.is_task_enabled("event_details_notification"):
        return

    engagement = getattr(event, "engagement", None)
    if not event.celebrant:
        return

    if engagement is None:
        from apps.notifications.services import queue_notification
        queue_notification(
            recipient_email=event.celebrant.email,
            recipient_user=event.celebrant,
            engagement=None,
            template_name="event_details_updated",
            context={"event_title": event.title, "what": what},
        )
        return

    debounce_seconds = PortalSettings.load().event_details_notify_debounce_seconds
    engagement.event_details_notify_token = uuid.uuid4()
    engagement.event_details_notify_due_at = timezone.now() + timedelta(seconds=debounce_seconds)
    engagement.event_details_notify_what = what
    engagement.save(update_fields=[
        "event_details_notify_token",
        "event_details_notify_due_at",
        "event_details_notify_what",
    ])

    # A couple of seconds of slack so the timer cannot wake a hair BEFORE
    # due_at, find `due_at__lte=now` false, and hand the email back to the cron
    # sweep it was meant to pre-empt.
    from .tasks import dispatch_due_event_details_notifications
    dispatch_due_event_details_notifications.schedule_in(
        debounce_seconds + 2,
        key=f"event_details:{engagement.pk}",
    )


def build_event_detail(event, request=None) -> dict:
    """
    Assemble everything the Event Details page needs in one shape — the event,
    its days, its contacts (grouped by category + per-category counts), and the
    planning stage (phase + progression + attribution) for THIS event's
    engagement. Mirrors document_hub.services.build_hub; lets the page load in a
    single call and gives the phase per selected event (not just the active one).

    Contacts/portal are imported lazily to avoid an events↔contacts import cycle.

    `request`, when supplied, is threaded into every serializer's context so
    image fields (the event cover, day images, contact photos) render as
    absolute URLs. With R2 storage the URL is absolute regardless, but under the
    in-memory storage used in tests a missing request yields a relative
    ``/media/...`` path a separate frontend origin can't resolve — so the view
    always passes it.
    """
    from apps.contacts.models import ContactCategory, EventContact
    from apps.contacts.serializers import EventContactListSerializer
    from apps.core.utils import user_display_name
    from apps.portal.services import PHASE_ORDER

    from .serializers import EventDaySerializer, EventSerializer

    contacts = EventContact.objects.filter(event=event)
    contacts_by_category = {}
    counts = []
    for value, label in ContactCategory.choices:
        in_cat = [c for c in contacts if c.category == value]
        counts.append({"category": value, "category_display": label, "count": len(in_cat)})
        if in_cat:
            contacts_by_category[value] = {
                "label": label,
                "contacts": EventContactListSerializer(
                    in_cat, many=True, context={"request": request}
                ).data,
            }

    engagement = getattr(event, "engagement", None)
    if engagement:
        phase = {
            "current_phase": engagement.current_phase,
            "current_phase_display": engagement.get_current_phase_display(),
            "phase_index": PHASE_ORDER.index(engagement.current_phase) + 1,
            "total_phases": len(PHASE_ORDER),
            "phase_details": engagement.phase_details,
            "phase_updated_by_display": user_display_name(engagement.phase_updated_by),
            "phase_updated_at": engagement.phase_updated_at,
            "event_details_locked": engagement.event_details_locked,
            "contacts_locked": engagement.contacts_locked,
        }
    else:
        phase = None

    return {
        "event": EventSerializer(event, context={"request": request}).data,
        "event_days": EventDaySerializer(
            event.days.select_related("owner").all(), many=True, context={"request": request}
        ).data,
        "contacts": contacts_by_category,
        "contacts_summary": counts,
        "planning_stage": phase,
    }


def get_event_deletion_impact(event) -> dict:
    """
    Count everything that would cascade-delete if this event were removed —
    see docs/FAILURE_POINTS_AUDIT.md F1. Event.delete() cascades through
    EventDay, EventContact, EventBudget, and (via the OneToOne
    EventEngagement) Meeting, Conversation, Reminder, Document,
    ClientDocument, PaymentSchedule/PaymentMilestone, Invoice, and Receipt.
    Used to warn before delete_event actually destroys any of it.
    """
    impact = {
        "event_days": event.days.count(),
        "event_contacts": event.contacts.count(),
    }

    budget = getattr(event, "budget", None)
    impact["budget_categories"] = budget.categories.count() if budget else 0
    impact["budget_payments"] = budget.payments.count() if budget else 0

    engagement = getattr(event, "engagement", None)
    if engagement:
        payment_schedule = getattr(engagement, "payment_schedule", None)
        impact["meetings"] = engagement.meetings.count()
        impact["conversations"] = engagement.conversations.count()
        impact["reminders"] = engagement.reminders.count()
        impact["documents"] = engagement.documents.count()
        impact["client_documents"] = engagement.client_documents.count()
        impact["invoices"] = engagement.invoices.count()
        impact["receipts"] = engagement.receipts.count()
        impact["payment_milestones"] = payment_schedule.milestones.count() if payment_schedule else 0
    else:
        impact.update({
            "meetings": 0, "conversations": 0, "reminders": 0, "documents": 0,
            "client_documents": 0, "invoices": 0, "receipts": 0, "payment_milestones": 0,
        })

    impact["total"] = sum(impact.values())
    return impact


def generate_event_title(event_type: str, data: dict) -> tuple[str | None, str | None]:
    """Generate title based on event type. Returns (title, error) tuple."""
    if event_type == "Wedding":
        groom = data.get("groom_name")
        bride = data.get("bride_name")
        if not groom or not bride:
            return None, "Both groom_name and bride_name are required for Weddings."
        return f"{groom} & {bride}'s Wedding", None
    elif event_type == "Birthday":
        honoree = data.get("honoree_name")
        if not honoree:
            return None, "honoree_name is required for Birthdays."
        return f"{honoree}'s Birthday", None
    elif event_type in ["Corporate", "Social Events", "Others"]:
        name = data.get("event_name")
        if not name:
            return None, "event_name is required for this event type."
        return name, None
    else:
        return None, "Valid event_type is required."
