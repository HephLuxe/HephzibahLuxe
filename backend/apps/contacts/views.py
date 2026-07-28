import os

from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.core.error_codes import CONTACTS_LOCKED, VALIDATION_ERROR
from apps.core.utils import save_with_attribution
from ..core.permissions import IsStaffOrSuperuser, can_access_event, enforce
from ..events.models import Event
from ..portal.models import ClientPortal

from .models import ContactCategory, EventContact
from .serializers import (
    EventContactCreateSerializer,
    EventContactListSerializer,
    EventContactSerializer,
)


# ── Envelope helper (see apps/core/exceptions.py for the exception-path half) ──

def _error(detail: str, code: str, http_status: int, errors: dict | None = None) -> Response:
    body: dict = {"detail": detail, "code": code}
    if errors:
        body["errors"] = errors
    return Response(body, status=http_status)


def _locked_error() -> Response:
    return _error(
        "Contacts are locked. Please contact your planning team to make changes.",
        CONTACTS_LOCKED,
        status.HTTP_403_FORBIDDEN,
    )


def _contacts_locked_for_event(event: Event) -> bool:
    """
    contacts_locked lives on EventEngagement, not ClientPortal — `event` carries
    a reverse OneToOne `.engagement` (may not exist yet for a staff-only event
    that has no engagement wired up), so read it from there.
    """
    engagement = getattr(event, "engagement", None)
    return bool(engagement and engagement.contacts_locked)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_contacts(request, event_slug):
    """
    List all contacts for an event, grouped by category.
    Staff sees any event's contacts. Client sees only their own event's contacts.
    Optional ?category= filter (e.g. ?category=primary).
    Optional ?event_day_id= filter (returns shared contacts + day-specific contacts).
    """
    event = get_object_or_404(Event, slug=event_slug)
    enforce(can_access_event(request.user, event))

    qs = EventContact.objects.filter(event=event)

    event_day_id = request.query_params.get("event_day_id")
    if event_day_id:
        qs = qs.filter(event_day_id=event_day_id)

    category = request.query_params.get("category")
    if category:
        qs = qs.filter(category=category)

    # Group by category for a richer response
    grouped = {}
    for choice_value, choice_label in ContactCategory.choices:
        contacts_in_cat = qs.filter(category=choice_value)
        if contacts_in_cat.exists():
            grouped[choice_value] = {
                "label": choice_label,
                "contacts": EventContactListSerializer(
                    contacts_in_cat, many=True, context={"request": request}
                ).data,
            }

    return Response(grouped)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def contacts_summary(request, event_slug):
    """
    Counts-only per category — for the Event Details Overview tab, which shows
    just the numbers (Primary 2, Decision Makers 1, ...) without the full
    contact detail (photos, phone, email) the Key Contacts tab needs.
    Optional ?event_day_id= filter, matching list_contacts.
    """
    event = get_object_or_404(Event, slug=event_slug)
    enforce(can_access_event(request.user, event))

    qs = EventContact.objects.filter(event=event)
    event_day_id = request.query_params.get("event_day_id")
    if event_day_id:
        qs = qs.filter(event_day_id=event_day_id)

    summary = [
        {
            "category": choice_value,
            "category_display": choice_label,
            "count": qs.filter(category=choice_value).count(),
        }
        for choice_value, choice_label in ContactCategory.choices
    ]
    return Response(summary)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_contact(request, event_slug):
    """
    Staff or client (event owner) can add a contact.
    Clients are blocked if contacts_locked is True on their portal.
    """
    event = get_object_or_404(Event, slug=event_slug)
    enforce(can_access_event(request.user, event))

    if not (request.user.is_staff or request.user.is_superuser) and _contacts_locked_for_event(event):
        return _locked_error()

    serializer = EventContactCreateSerializer(data=request.data, context={"event": event})
    if not serializer.is_valid():
        return _error("Invalid contact data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    event_day = serializer.validated_data.get("event_day")
    if event_day and event_day.owner != event:
        return _error(
            "This day does not belong to the specified event.",
            VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
            errors={"event_day": ["This day does not belong to the specified event."]},
        )

    contact = save_with_attribution(serializer, request.user, event=event)
    return Response(
        EventContactSerializer(contact, context={"request": request}).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def contact_detail(request, event_slug, contact_id):
    """
    GET    — staff or event owner can view a single contact.
    PATCH  — staff always; client only if contacts are not locked.
    DELETE — staff always; client only if contacts are not locked.
    """
    event = get_object_or_404(Event, slug=event_slug)
    contact = get_object_or_404(EventContact, id=contact_id, event=event)

    if request.method == "GET":
        enforce(can_access_event(request.user, event))
        return Response(EventContactSerializer(contact, context={"request": request}).data)

    # PATCH and DELETE share the same access rule
    enforce(can_access_event(request.user, event))

    if not (request.user.is_staff or request.user.is_superuser) and _contacts_locked_for_event(event):
        return _locked_error()

    if request.method == "DELETE":
        contact.delete()
        return Response({"detail": "Contact deleted."})

    # PATCH
    serializer = EventContactCreateSerializer(
        contact, data=request.data, partial=True, context={"event": event}
    )
    if not serializer.is_valid():
        return _error("Invalid contact data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    event_day = serializer.validated_data.get("event_day")
    if event_day and event_day.owner != event:
        return _error(
            "This day does not belong to the specified event.",
            VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
            errors={"event_day": ["This day does not belong to the specified event."]},
        )

    contact = save_with_attribution(serializer, request.user)
    return Response(EventContactSerializer(contact, context={"request": request}).data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def toggle_contacts_lock(request):
    """
    Staff only: lock or unlock contact editing for a client's active engagement.
    Body: { "portal_id": "<uuid>", "contacts_locked": true }

    contacts_locked lives on EventEngagement (not ClientPortal) since locking
    is scoped to a specific event engagement, not the portal as a whole.
    """
    portal_id = request.data.get("portal_id")
    if not portal_id:
        return _error("portal_id is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    portal = get_object_or_404(ClientPortal, id=portal_id)
    locked = request.data.get("contacts_locked")
    if locked is None:
        return _error("contacts_locked is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    engagement = portal.active_engagement
    if engagement is None:
        return _error("This portal has no active engagement.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    engagement.contacts_locked = locked
    engagement.save(update_fields=["contacts_locked", "updated_at"])
    return Response({
        "detail": f"Contacts {'locked' if locked else 'unlocked'} successfully.",
        "contacts_locked": engagement.contacts_locked,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def copy_contacts_from_day(request, event_slug):
    """
    Copy all contacts from one event day to another.
    Body: { "source_event_day_id": 1, "target_event_day_id": 2 }
    Skips contacts that would violate the unique constraint.
    """
    event = get_object_or_404(Event, slug=event_slug)
    enforce(can_access_event(request.user, event))

    if not (request.user.is_staff or request.user.is_superuser) and _contacts_locked_for_event(event):
        return _locked_error()

    source_id = request.data.get("source_event_day_id")
    target_id = request.data.get("target_event_day_id")

    if not source_id or not target_id:
        return _error(
            "source_event_day_id and target_event_day_id are required.",
            VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )

    if source_id == target_id:
        return _error(
            "Source and target event days cannot be the same.",
            VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )

    from ..events.models import EventDay
    source_day = get_object_or_404(EventDay, id=source_id, owner=event)
    target_day = get_object_or_404(EventDay, id=target_id, owner=event)

    source_contacts = EventContact.objects.filter(event=event, event_day=source_day)
    copied, skipped = 0, 0

    for contact in source_contacts:
        # Skip if same email+category already exists on target day
        already_exists = (
            contact.email
            and EventContact.objects.filter(
                event=event,
                event_day=target_day,
                category=contact.category,
                email=contact.email,
            ).exists()
        )
        if already_exists:
            skipped += 1
            continue

        new_contact = EventContact.objects.create(
            event=event,
            event_day=target_day,
            category=contact.category,
            name=contact.name,
            role=contact.role,
            phone=contact.phone,
            email=contact.email,
            preferred_method=contact.preferred_method,
            created_by=request.user,
            last_updated_by=request.user,
        )
        if contact.photo:
            # Duplicate the actual file bytes onto the new contact's own photo
            # field — assigning `photo=contact.photo` directly would only copy
            # the stored path string, leaving both contacts pointing at the
            # same physical file (see docs/FAILURE_POINTS_AUDIT.md F6). Calling
            # .save() here re-runs contact_photo_upload_path for the new
            # contact's own id, producing an independent copy.
            new_contact.photo.save(
                os.path.basename(contact.photo.name),
                ContentFile(contact.photo.read()),
                save=True,
            )
        copied += 1

    return Response({
        "detail": f"{copied} contact(s) copied to {target_day.event_day_title}.",
        "copied": copied,
        "skipped": skipped,
    }, status=status.HTTP_201_CREATED)
