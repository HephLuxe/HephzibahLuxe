# apps/events/services.py

import uuid


def schedule_event_details_notification(event, what: str) -> None:
    """
    Debounced "event details updated" client email. Every edit to this event OR
    any of its EventDays calls this — rather than emailing immediately per save,
    it stamps a fresh token on the engagement and schedules a delayed send
    (portal.PortalSettings.event_details_notify_debounce_seconds, admin-
    configurable, default 15 min). If another edit lands before that delay
    elapses, the token changes and the earlier, now-stale task no-ops when it
    fires — only the LAST scheduled task's token still matches, so it's the one
    that actually sends. A burst of edits therefore collapses into exactly one
    email, describing the most recent change, sent once the editor has been
    quiet for the full window — never a fixed deadline the editor has to beat,
    since editing again just pushes the send further out.

    Falls back to sending immediately if the event has no engagement yet (no
    row to key the debounce token on — rare: engagements are created alongside
    events in the normal flow) or no celebrant to notify.
    """
    from apps.notifications.models import ScheduledTaskSettings
    from apps.portal.models import PortalSettings

    from .tasks import send_event_details_updated_notification_task

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

    token = uuid.uuid4()
    engagement.event_details_notify_token = token
    engagement.save(update_fields=["event_details_notify_token"])

    debounce_seconds = PortalSettings.load().event_details_notify_debounce_seconds
    send_event_details_updated_notification_task.apply_async(
        args=[str(engagement.id), str(token), what],
        countdown=debounce_seconds,
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
