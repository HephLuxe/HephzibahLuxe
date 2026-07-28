"""
apps/core/deeplinks.py

The registry that turns a *domain object* into a portal deep link.

A reminder (and, later, anything else that wants to point a client at
something) stores a reference to the actual target object — not a
staff-typed URL string. The URL is derived from that object at read time,
here. That buys three things a free-text ``link_url`` cannot:

  * the link always points at a real row (a deleted target resolves to None
    rather than a 404 the client discovers by clicking);
  * the link cannot be aimed at another client's data — every target knows
    how to report its engagement, and callers check it against the
    reminder's own engagement (see reminders.services.resolve_target);
  * the route lives in exactly one place, so a frontend route rename is a
    one-line change here rather than a hunt through stored strings.

``TARGET_TYPES`` keys are the public API values staff/clients send as
``target_type`` — keep them stable, they are part of the API contract.

Route paths mirror the portal's frontend routes. The object's identifier is
passed as a query param; the page is responsible for selecting/scrolling to
it. Anything not listed here simply isn't linkable yet — add an entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Model
from rest_framework.exceptions import ValidationError


@dataclass(frozen=True)
class TargetSpec:
    """How to link to — and authorise — one kind of target object."""

    model_label: str                          # "app_label.ModelName" (lazy: avoids import cycles)
    url: Callable[[Model], str]               # obj -> portal route
    engagement: Callable[[Model], object]     # obj -> EventEngagement | None (ownership check)
    label: Callable[[Model], str]             # obj -> sensible default link_label

    def get_model(self) -> type[Model]:
        return apps.get_model(self.model_label)


# ── Engagement resolvers ─────────────────────────────────────────
# Each target must be able to name the engagement it belongs to, or the
# ownership check in resolve_target() cannot be made. Written defensively:
# an event with no engagement wired up yet (see FAILURE_POINTS_AUDIT F3/F7)
# returns None, which callers treat as "not linkable", never as "allowed".

def _engagement_of_event(event):
    return getattr(event, "engagement", None)


TARGET_TYPES: dict[str, TargetSpec] = {
    # ── Questions & Clarifications ──
    "conversation": TargetSpec(
        model_label="conversations.Conversation",
        url=lambda o: f"/portal/questions?conversationId={o.pk}",
        engagement=lambda o: o.engagement,
        label=lambda o: "View conversation",
    ),

    # ── Meetings ──
    "meeting": TargetSpec(
        model_label="meetings.Meeting",
        url=lambda o: f"/portal/meetings?meetingId={o.pk}",
        engagement=lambda o: o.engagement,
        label=lambda o: "View meeting",
    ),
    "prep_item": TargetSpec(
        model_label="meetings.MeetingPrepItem",
        url=lambda o: f"/portal/meetings?meetingId={o.meeting_id}&prepItemId={o.pk}",
        engagement=lambda o: o.meeting.engagement,
        label=lambda o: "Complete preparation",
    ),

    # ── Event details ──
    "event": TargetSpec(
        model_label="events.Event",
        url=lambda o: f"/portal/event-details?event={o.slug}",
        engagement=_engagement_of_event,
        label=lambda o: "View event details",
    ),
    "event_day": TargetSpec(
        model_label="events.EventDay",
        url=lambda o: f"/portal/event-details?event={o.owner.slug}&dayId={o.pk}",
        engagement=lambda o: _engagement_of_event(o.owner),
        label=lambda o: "View event day",
    ),
    "event_contact": TargetSpec(
        model_label="contacts.EventContact",
        url=lambda o: f"/portal/event-details?event={o.event.slug}&tab=contacts&contactId={o.pk}",
        engagement=lambda o: _engagement_of_event(o.event),
        label=lambda o: "View contact",
    ),

    # ── Document hub (client ↔ Hephzibah Luxe) ──
    "client_document": TargetSpec(
        model_label="document_hub.ClientDocument",
        url=lambda o: f"/portal/contracts/client?documentId={o.pk}",
        engagement=lambda o: o.engagement,
        label=lambda o: "View document",
    ),
    "invoice": TargetSpec(
        model_label="document_hub.Invoice",
        url=lambda o: f"/portal/financials/client?invoiceId={o.pk}",
        engagement=lambda o: o.engagement,
        label=lambda o: "View invoice",
    ),
    "receipt": TargetSpec(
        model_label="document_hub.Receipt",
        url=lambda o: f"/portal/financials/client?receiptId={o.pk}",
        engagement=lambda o: o.engagement,
        label=lambda o: "View receipt",
    ),
    "payment_milestone": TargetSpec(
        model_label="document_hub.PaymentMilestone",
        url=lambda o: f"/portal/financials/client?milestoneId={o.pk}",
        engagement=lambda o: o.schedule.engagement,
        label=lambda o: "View payment",
    ),

    # ── Event budget ──
    "budget_payment": TargetSpec(
        model_label="budgets.BudgetPayment",
        url=lambda o: f"/portal/financials/budget?paymentId={o.pk}",
        engagement=lambda o: _engagement_of_event(o.budget.event),
        label=lambda o: "View payment",
    ),
}


# Reverse index: "app_label.modelname" (lowercased, as Django's _meta reports
# it) -> the public target_type slug.
_TYPE_BY_MODEL_LABEL = {
    spec.model_label.lower(): target_type for target_type, spec in TARGET_TYPES.items()
}


def spec_for_type(target_type: str) -> TargetSpec | None:
    """Look up a spec by its public ``target_type`` slug."""
    return TARGET_TYPES.get(target_type)


def type_for_instance(obj: Model) -> str | None:
    """The public ``target_type`` slug for this object, if it is linkable."""
    return _TYPE_BY_MODEL_LABEL.get(f"{obj._meta.app_label}.{obj._meta.model_name}")


def spec_for_instance(obj: Model) -> TargetSpec | None:
    """Reverse lookup: find the spec registered for this object's model."""
    target_type = type_for_instance(obj)
    return TARGET_TYPES[target_type] if target_type else None


def build_url(obj: Model) -> str | None:
    """The portal route that displays this object, or None if it isn't linkable."""
    spec = spec_for_instance(obj)
    return spec.url(obj) if spec else None


def default_label(obj: Model) -> str | None:
    """A sensible link label for this object, used when staff supply none."""
    spec = spec_for_instance(obj)
    return spec.label(obj) if spec else None


def engagement_of(obj: Model):
    """
    The engagement this object belongs to, or None when it has none (an event
    with no engagement, an orphaned meeting). None must never be read as
    "allowed" — see resolve_target.
    """
    spec = spec_for_instance(obj)
    if spec is None:
        return None
    try:
        return spec.engagement(obj)
    except (AttributeError, ObjectDoesNotExist):
        return None


# Standalone portal routes that aren't tied to a target object.
LOGIN_PATH = "/login"


def absolute_url(path: str | None) -> str | None:
    """
    Turn a portal route ("/portal/questions?…") into a full URL for use outside
    the app — emails, mainly, where a relative path is meaningless.

    Returns None only when `path` itself is empty (a reminder with no deep
    link), never because of configuration: FRONTEND_BASE_URL is validated at
    boot, so there is no "unset base" case to degrade around.
    """
    if not path:
        return None
    base = settings.FRONTEND_BASE_URL
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def login_url() -> str:
    """
    Absolute URL of the portal's login page (the credentials email's CTA).
    Derived from FRONTEND_BASE_URL rather than configured separately, and never
    defaulted in code — see the note on that setting.
    """
    return absolute_url(LOGIN_PATH)


def resolve_target(engagement, target_type: str, target_id: str) -> Model:
    """
    Turn a (target_type, target_id) pair from the API into the actual object to
    deep-link to — refusing anything that isn't this engagement's to link.

    This is the whole point of storing a target instead of a URL string. A
    staff-typed "/portal/questions?conversationId=<uuid>" is unverifiable: the
    id may be a typo, may name a deleted row, or may belong to a different
    client's portal, and the backend would happily hand it to the client either
    way. Here, all three are caught before anything is saved.

    Shared by reminders (Reminder.target) and conversations (the `links` array).
    Raises rest_framework ValidationError; returns the target object.
    """
    spec = spec_for_type(target_type)
    if spec is None:
        valid = ", ".join(sorted(TARGET_TYPES))
        raise ValidationError(f"'{target_type}' is not a linkable target type. Valid types: {valid}.")

    try:
        target = spec.get_model().objects.filter(pk=target_id).first()
    except (ValueError, TypeError, DjangoValidationError):
        # e.g. a non-UUID string passed as the pk of a UUID-PK model
        raise ValidationError(f"'{target_id}' is not a valid id for target type '{target_type}'.")

    if target is None:
        raise ValidationError(f"No {target_type} found with id '{target_id}'.")

    target_engagement = engagement_of(target)
    # None means the target has no engagement at all (e.g. an event that was
    # never wired to a portal) — never treat that as "belongs to everyone".
    if target_engagement is None or engagement is None or target_engagement.pk != engagement.pk:
        raise ValidationError(
            f"That {target_type} does not belong to this engagement — a link "
            "can only point at the client's own data."
        )

    return target
