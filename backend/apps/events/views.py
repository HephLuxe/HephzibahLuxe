import logging

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.error_codes import (
    CONFIRMATION_REQUIRED,
    EVENT_DETAILS_LOCKED,
    NOT_FOUND,
    VALIDATION_ERROR,
)
from apps.core.pagination import StandardPageNumberPagination
from apps.core.utils import save_with_attribution
from apps.portal.models import ClientPortal, EventEngagement

from ..core.permissions import IsStaffOrSuperuser, can_access_event, enforce, is_staff_or_superuser
from . import services
from .models import Event, EventDay
from .serializers import EventDaySerializer, EventSerializer
from .services import build_event_detail, generate_event_title, get_event_deletion_impact

logger = logging.getLogger(__name__)


# ── Envelope helper (see apps/core/exceptions.py for the exception-path half) ──

def _error(detail: str, code: str, http_status: int, errors: dict | None = None) -> Response:
    body: dict = {"detail": detail, "code": code}
    if errors:
        body["errors"] = errors
    return Response(body, status=http_status)


def _list_response(request, queryset, serializer_cls):
    """
    Serialize a list into the standard ``{count, next, previous, results}``
    envelope. Request context is always passed so image URLs render absolute.

    ALWAYS paginated. This used to opt in only when the caller passed ``?page=``
    or ``?page_size=``, which meant the default response serialised the entire
    table — for a staff caller, every event on the platform in one payload. Rate
    limiting caps how MANY requests a caller makes, not how much each one hands
    over, so an unbounded list needs exactly one request to give up everything
    it can see; bounding the page is the control that actually applies. Same
    reasoning as GET /inquiries/ and GET /users/.

    Both callers are role-scoped already (a client sees only their own events),
    so this is not about cross-tenant exposure — it is about a staff token, and
    about a response whose size grows with the business.

    This DID change the response shape: the unpaginated form returned a bare
    JSON array, so callers move from ``res.map(...)`` to ``res.results.map(...)``.
    That is why it was not carried over with the /inquiries/ fix — that endpoint
    already returned ``{count, results}`` and could be migrated invisibly. The
    page size is unchanged from what ``?page=1`` already produced, so only the
    default path moves.

    Ordering is forced when the caller has not set any, and that is a
    correctness requirement rather than tidiness — it only becomes a bug once a
    list is paged. Postgres guarantees no row order without ORDER BY, so
    LIMIT/OFFSET over an unordered queryset can hand the same row out on two
    pages and skip another entirely. Unpaginated, this was invisible: every row
    came back regardless of order. ``-created_at`` is the meaningful key and
    ``-id`` breaks ties, since two rows sharing an auto_now_add tick would
    otherwise still be non-deterministic.
    """
    if not queryset.ordered:
        queryset = queryset.order_by("-created_at", "-id")

    paginator = StandardPageNumberPagination()
    page = paginator.paginate_queryset(queryset, request)
    data = serializer_cls(page, many=True, context={"request": request}).data
    return paginator.get_paginated_response(data)


def _event_details_locked_for_event(event: Event) -> bool:
    """
    event_details_locked lives on EventEngagement (not Event) — mirrors
    contacts._contacts_locked_for_event. `event` carries a reverse OneToOne
    `.engagement` that may not exist yet for an event with no engagement wired
    up (see F3/F7 in docs/FAILURE_POINTS_AUDIT.md), so read it defensively.
    """
    engagement = getattr(event, "engagement", None)
    return bool(engagement and engagement.event_details_locked)


def _locked_error() -> Response:
    return _error(
        "Event details are locked. Please contact your planning team to make changes.",
        EVENT_DETAILS_LOCKED,
        status.HTTP_403_FORBIDDEN,
    )


###############################################     EVENT       ###############################################

@api_view(['POST'])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def create_event(request):
    """
    Staff only: create a new event for a client.
    Looks up the client by `user_email` from the request body.
    Generates the event title automatically based on `event_type` and the
    relevant name fields (groom/bride, honoree, etc.).

    Every event gets its own EventEngagement automatically — active only if
    the portal has no other active engagement yet (e.g. the client's first
    event), inactive otherwise. This means staff can pre-stage a future
    event (meetings, conversations, documents, etc.) before switching to it
    with PATCH /portal/activate-event/ — nothing has to be created at
    switch time, activation just flips which engagement is_active.
    """
    data = request.data.copy()
    event_type = data.get('event_type')
    user_email = data.get('user_email')

    User = get_user_model()
    try:
        celebrant = User.objects.get(email=user_email)
    except User.DoesNotExist:
        return _error("User with this email does not exist. Provide a valid email.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    title, error = generate_event_title(event_type, data)
    if error:
        return _error(error, VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    data['title'] = title

    serializer = EventSerializer(data=data, context={"request": request})
    if not serializer.is_valid():
        return _error("Invalid event data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    event = save_with_attribution(serializer, request.user, celebrant=celebrant)

    # Every event gets an engagement now (see docs/FAILURE_POINTS_AUDIT.md F3
    # addendum) — active only if the portal doesn't already have one active.
    try:
        portal = ClientPortal.objects.get(user=celebrant)
        should_be_active = not portal.engagements.filter(is_active=True).exists()
        try:
            EventEngagement.objects.create(portal=portal, event=event, is_active=should_be_active)
        except IntegrityError:
            # Lost the race for "first active engagement"
            # (unique_active_engagement_per_portal — see
            # apps/portal/models.py EventEngagement.Meta.constraints): another
            # concurrent create_event call for this portal already claimed
            # is_active=True between our check above and this create(). Every
            # event still gets an engagement — just inactive instead.
            logger.info(
                "Lost race for portal's active engagement — creating this event's engagement as inactive instead.",
                extra={"event_id": event.id, "event_slug": event.slug, "celebrant_email": celebrant.email},
            )
            EventEngagement.objects.create(portal=portal, event=event, is_active=False)
    except ClientPortal.DoesNotExist:
        # Event is created either way, but with no portal it can never get an
        # engagement — it will exist yet stay invisible in getPortal until
        # someone notices and follows up. Surface it instead of passing silently.
        logger.warning(
            "Event created for celebrant with no ClientPortal — event has no engagement.",
            extra={"event_id": event.id, "event_slug": event.slug, "celebrant_email": celebrant.email},
        )

    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_event(request, slug):
    """
    GET — retrieve a single event by slug.
    Staff or the event's celebrant can access. Anyone else gets 403.
    """
    try:
        event = Event.objects.get(slug=slug)
    except Event.DoesNotExist:
        return _error("Event with this slug does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event), "You don't have permission to view this event")

    return Response(EventSerializer(event, context={"request": request}).data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def event_detail_aggregate(request, slug):
    """
    GET — the whole Event Details page in one call: event + days + contacts
    (grouped + summary counts) + planning stage (phase for THIS event).
    Staff or the event's celebrant. See services.build_event_detail.
    """
    try:
        event = Event.objects.get(slug=slug)
    except Event.DoesNotExist:
        return _error("Event with this slug does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event), "You don't have permission to view this event")

    return Response(build_event_detail(event, request=request), status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def getall_event(request):
    """
    GET — list all events.
    Staff/superusers see every event. Clients see only their own events.
    """
    if is_staff_or_superuser(request.user):
        events = Event.objects.select_related("celebrant").all()
    else:
        events = Event.objects.select_related("celebrant").filter(celebrant=request.user)

    return _list_response(request, events, EventSerializer)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def getall_event_email(request, email):
    """
    GET — list all events belonging to a specific user, looked up by email.
    Staff/superusers can look up any user. Clients can only look up themselves.
    """
    User = get_user_model()
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return _error("User with this email does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(is_staff_or_superuser(request.user) or request.user == user, "You don't have permission to view this user's events")

    events = Event.objects.select_related("celebrant").filter(celebrant=user)
    return Response(
        EventSerializer(events, many=True, context={"request": request}).data,
        status=status.HTTP_200_OK,
    )


@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_event(request, slug):
    """
    PUT/PATCH — update an event by slug.
    Staff can always update. The event's celebrant can update unless staff has
    set event_details_locked on the active engagement.
    PATCH supports partial updates; PUT requires all fields.
    """
    try:
        event = Event.objects.get(slug=slug)
    except Event.DoesNotExist:
        return _error("Event with this slug does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event), "You don't have permission to update this event")

    if not is_staff_or_superuser(request.user) and _event_details_locked_for_event(event):
        return _locked_error()

    partial = request.method == 'PATCH'
    serializer = EventSerializer(event, data=request.data, partial=partial, context={"request": request})
    if not serializer.is_valid():
        return _error("Invalid event data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)
    services.schedule_event_details_notification(event, "Event details")
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def get_event_delete_impact(request, slug):
    """
    Staff only: preview what deleting this event would cascade-destroy,
    without actually deleting anything. Intended to back a confirmation
    dialog before calling DELETE /event/delete/<slug>/ — see
    docs/FAILURE_POINTS_AUDIT.md F1.
    """
    try:
        event = Event.objects.get(slug=slug)
    except Event.DoesNotExist:
        return _error("Event with this slug does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    return Response(get_event_deletion_impact(event))


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def delete_event(request, slug):
    """
    Staff only — clients can never delete an event, regardless of
    event_details_locked (deletion carries its own cascade risk independent
    of the edit lock; see docs/FAILURE_POINTS_AUDIT.md F1/F2).

    DELETE — delete an event by slug. Cascades to all related EventDays,
    EventContacts, and (via EventEngagement) meetings/conversations/reminders/
    documents automatically. If the event has any related data attached,
    the request is refused unless ?confirm=true is passed — the response
    body carries the same impact breakdown as get_event_delete_impact so the
    caller can show it to the operator first. A genuinely empty event (no
    related data at all — e.g. a just-created duplicate) deletes immediately,
    no confirmation needed.
    """
    try:
        event = Event.objects.get(slug=slug)
    except Event.DoesNotExist:
        return _error("Event with this slug does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event), "You don't have permission to delete this event")

    impact = get_event_deletion_impact(event)
    confirmed = request.query_params.get("confirm", "").lower() == "true"
    if impact["total"] > 0 and not confirmed:
        return _error(
            "This event has related data attached. Review the impact and pass ?confirm=true to delete anyway.",
            CONFIRMATION_REQUIRED,
            status.HTTP_400_BAD_REQUEST,
            errors={"impact": impact},
        )

    event.delete()
    return Response({"detail": "Event deleted successfully."}, status=status.HTTP_200_OK)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def toggle_event_details_lock(request):
    """
    Staff only: lock or unlock event/event-day editing for a client's active
    engagement. Body: { "portal_id": "<uuid>", "event_details_locked": true }

    event_details_locked lives on EventEngagement (not Event), same reasoning
    as contacts_locked — scoped to a specific engagement, not the portal as a
    whole. Independent of contacts_locked: locking one does not lock the
    other. Does not affect delete_event/delete_eventday, which are always
    staff-only regardless of this flag.
    """
    portal_id = request.data.get("portal_id")
    if not portal_id:
        return _error("portal_id is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    portal = ClientPortal.objects.filter(id=portal_id).first()
    if not portal:
        return _error("Portal not found.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    locked = request.data.get("event_details_locked")
    if locked is None:
        return _error("event_details_locked is required.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    engagement = portal.active_engagement
    if engagement is None:
        return _error("This portal has no active engagement.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST)

    engagement.event_details_locked = locked
    engagement.save(update_fields=["event_details_locked", "updated_at"])
    return Response({
        "detail": f"Event details {'locked' if locked else 'unlocked'} successfully.",
        "event_details_locked": engagement.event_details_locked,
    })


###############################################     EVENT_DAY       ###############################################

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_eventday(request, event_slug):
    """
    POST — add a new event day to an event.
    Staff or the event's celebrant can create.
    The new day is automatically linked to the event via `owner`.
    """
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        return _error("Event with this title does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event))

    serializer = EventDaySerializer(data=request.data, context={"request": request})
    if not serializer.is_valid():
        return _error("Invalid event day data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user, owner=event)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def getall_eventday(request):
    """
    GET — list all event days.
    Staff/superusers see every event day. Clients see only days belonging to their own events.
    """
    if is_staff_or_superuser(request.user):
        eventdays = EventDay.objects.select_related("owner", "owner__celebrant").all()
    else:
        eventdays = EventDay.objects.select_related("owner", "owner__celebrant").filter(
            owner__celebrant=request.user
        )

    return _list_response(request, eventdays, EventDaySerializer)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def getall_eventday_email(request, email):
    """
    GET — list all event days belonging to a specific user, looked up by email.
    Staff/superusers can look up any user. Clients can only look up themselves.
    """
    User = get_user_model()
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return _error("User with this email does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(is_staff_or_superuser(request.user) or request.user == user)

    event_days = EventDay.objects.select_related("owner", "owner__celebrant").filter(
        owner__celebrant=user
    )
    return Response(
        EventDaySerializer(event_days, many=True, context={"request": request}).data,
        status=status.HTTP_200_OK,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_eventday_id(request, event_slug, id):
    """
    GET — retrieve a single event day by its ID, scoped to an event slug.
    Validates that the event day actually belongs to the given event.
    Staff or the event's celebrant can access.
    """
    try:
        event = Event.objects.get(slug=event_slug)
        eventday = EventDay.objects.get(id=id, owner=event)  # Validates relationship
    except (Event.DoesNotExist, EventDay.DoesNotExist):
        return _error("Event Day not found or doesn't belong to this event.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event))

    return Response(EventDaySerializer(eventday, context={"request": request}).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def getall_eventday_slug(request, event_slug):
    """
    GET — list all event days for a specific event, identified by slug.
    Staff or the event's celebrant can access.
    Results are ordered by date and start_time (defined on the model's Meta).
    """
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        return _error("Event with this slug does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event))

    return Response(
        EventDaySerializer(
            event.days.select_related("owner").all(), many=True, context={"request": request}
        ).data,
        status=status.HTTP_200_OK,
    )


@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_eventday(request, event_slug, id):
    """
    PUT/PATCH — update an event day by ID, scoped to an event slug.
    Staff can always update. The event's celebrant can update unless staff has
    set event_details_locked on the active engagement.
    PATCH supports partial updates; PUT requires all fields.
    """
    try:
        event = Event.objects.get(slug=event_slug)
        eventday = EventDay.objects.get(id=id, owner=event)  # Validates relationship
    except (Event.DoesNotExist, EventDay.DoesNotExist):
        return _error("Event Day not found or doesn't belong to this event.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event), "You don't have permission to update this event day")

    if not is_staff_or_superuser(request.user) and _event_details_locked_for_event(event):
        return _locked_error()

    partial = request.method == 'PATCH'
    serializer = EventDaySerializer(eventday, data=request.data, partial=partial, context={"request": request})
    if not serializer.is_valid():
        return _error("Invalid event day data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)
    services.schedule_event_details_notification(event, f"Details for {eventday.event_day_title or 'an event day'}")
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsStaffOrSuperuser])
def delete_eventday(request, event_slug, id):
    """
    Staff only — clients can never delete an event day, regardless of
    event_details_locked. Deleting an EventDay cascades to its EventContact
    rows (see docs/FAILURE_POINTS_AUDIT.md F1/F2).
    DELETE — delete an event day by ID, scoped to an event slug.
    """
    try:
        event = Event.objects.get(slug=event_slug)
        eventday = EventDay.objects.get(id=id, owner=event)  # Validates relationship
    except (Event.DoesNotExist, EventDay.DoesNotExist):
        return _error("Event Day not found or doesn't belong to this event.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event), "You don't have permission to delete this event day")

    eventday.delete()
    return Response({"detail": "Event Day deleted successfully."}, status=status.HTTP_200_OK)
