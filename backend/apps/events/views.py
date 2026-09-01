import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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
from .models import Event, EventDay, EventImage
from .serializers import EventDaySerializer, EventImageSerializer, EventSerializer
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
        events = Event.objects.select_related("celebrant").prefetch_related("images").all()
    else:
        events = Event.objects.select_related("celebrant").prefetch_related("images").filter(celebrant=request.user)

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

    events = Event.objects.select_related("celebrant").prefetch_related("images").filter(celebrant=user)
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
        eventdays = EventDay.objects.select_related("owner", "owner__celebrant").prefetch_related("images").all()
    else:
        eventdays = EventDay.objects.select_related("owner", "owner__celebrant").prefetch_related("images").filter(
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

    event_days = EventDay.objects.select_related("owner", "owner__celebrant").prefetch_related("images").filter(
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
            event.days.select_related("owner").prefetch_related("images").all(), many=True, context={"request": request}
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


###############################################     GALLERY       ###############################################
#
# One set of routes for both galleries rather than two parallel sets: every
# EventImage carries `event`, so the event slug in the URL is always the scope
# that matters for permissions, and `event_day` in the body (or `?event_day=`
# on the read) narrows it to a day. Two route families would have duplicated
# the lookup, the permission check and the lock gate four more times.


def _resolve_gallery_scope(event, raw_day_id):
    """
    Turn a request's `event_day` value into an EventDay of THIS event, or None
    for the event-level gallery.

    Returns (event_day, error_response). The membership check is the reason this
    isn't inlined: without it a caller could attach an image to any day on the
    platform by naming its id, since the day id is the only thing identifying the
    target and it isn't otherwise tied to the slug in the URL.
    """
    if raw_day_id in (None, "", "null"):
        return None, None
    try:
        return EventDay.objects.get(id=raw_day_id, owner=event), None
    except (EventDay.DoesNotExist, ValidationError, ValueError, TypeError):
        return None, _error(
            "Event Day not found or doesn't belong to this event.",
            NOT_FOUND, status.HTTP_404_NOT_FOUND,
        )


def _gallery_queryset(event, event_day):
    qs = EventImage.objects.filter(event=event)
    return qs.filter(event_day=event_day) if event_day else qs.filter(event_day__isnull=True)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def event_gallery(request, event_slug):
    """
    GET  — list a gallery. `?event_day=<uuid>` for a day's images, omitted for
           the event's own. Not paginated: a gallery is a page's worth of photos
           by construction, and the frontend needs all of them to lay out the
           grid, so splitting it across requests would only cause the page to
           render in pieces.
    POST — upload one or more images (multipart, repeat the `image` key per
           file). `event_day` in the body targets a day's gallery.

    Staff can always write. The celebrant can too, unless staff has set
    event_details_locked — the same gate as editing the event itself, because a
    locked event whose photographs are still replaceable isn't locked.
    """
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        return _error("Event with this title does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event), "You don't have permission to access this event")

    if request.method == 'GET':
        event_day, error = _resolve_gallery_scope(event, request.query_params.get("event_day"))
        if error:
            return error
        images = _gallery_queryset(event, event_day)
        return Response(
            EventImageSerializer(images, many=True, context={"request": request}).data,
        )

    if not is_staff_or_superuser(request.user) and _event_details_locked_for_event(event):
        return _locked_error()

    event_day, error = _resolve_gallery_scope(event, request.data.get("event_day"))
    if error:
        return error

    files = request.FILES.getlist("image")
    if not files:
        return _error(
            "No image supplied. Send one or more files under the `image` key.",
            VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
        )

    # Validate every file BEFORE saving any of them, so a batch with one
    # oversized photo is refused whole rather than leaving the first few
    # uploaded and the caller unsure how far it got.
    serializers_ = []
    for index, upload in enumerate(files):
        serializer = EventImageSerializer(
            data={"image": upload, "alt_text": request.data.get("alt_text", "")},
            context={"request": request},
        )
        if not serializer.is_valid():
            return _error(
                f"Invalid image at position {index + 1}.",
                VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors,
            )
        serializers_.append(serializer)

    created = []
    with transaction.atomic():
        for serializer in serializers_:
            created.append(save_with_attribution(
                serializer, request.user,
                event=event, event_day=event_day,
                sort_order=services.next_sort_order(event, event_day),
            ))
        services.ensure_gallery_has_a_cover(event, event_day)

    for image in created:
        image.refresh_from_db()

    services.schedule_event_details_notification(event, _gallery_change_description(event_day))
    return Response(
        EventImageSerializer(created, many=True, context={"request": request}).data,
        status=status.HTTP_201_CREATED,
    )


def _gallery_change_description(event_day) -> str:
    if event_day:
        return f"Photographs for {event_day.event_day_title or 'an event day'}"
    return "Event photographs"


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def event_gallery_image(request, event_slug, image_id):
    """
    PATCH  — edit one image: `alt_text`, `sort_order`, or `is_primary: true` to
             make it the gallery cover. Promotion goes through
             services.set_primary_image so the previous cover is demoted in the
             same transaction; setting the flag directly would trip the partial
             unique constraint.
    DELETE — remove the image and its stored blob (the blob via the post_delete
             receiver in signals.py). If it was the cover, the next image in the
             gallery is promoted so the event/day doesn't render coverless.

    Unlike delete_eventday, a client may delete their own images when the event
    is unlocked — a photograph is content they supplied, not scheduling data,
    and the event day it hangs off survives.
    """
    try:
        event = Event.objects.get(slug=event_slug)
        image = EventImage.objects.select_related("event", "event_day").get(
            id=image_id, event=event,
        )
    except (Event.DoesNotExist, EventImage.DoesNotExist):
        return _error(
            "Image not found or doesn't belong to this event.",
            NOT_FOUND, status.HTTP_404_NOT_FOUND,
        )

    enforce(can_access_event(request.user, event), "You don't have permission to modify this image")

    if not is_staff_or_superuser(request.user) and _event_details_locked_for_event(event):
        return _locked_error()

    event_day = image.event_day

    if request.method == 'DELETE':
        image.delete()
        services.ensure_gallery_has_a_cover(event, event_day)
        services.schedule_event_details_notification(event, _gallery_change_description(event_day))
        return Response({"detail": "Image deleted successfully."}, status=status.HTTP_200_OK)

    data = {k: v for k, v in request.data.items() if k in ("alt_text", "sort_order")}
    serializer = EventImageSerializer(
        image, data=data, partial=True, context={"request": request},
    )
    if not serializer.is_valid():
        return _error("Invalid image data.", VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST, errors=serializer.errors)

    save_with_attribution(serializer, request.user)

    # is_primary is handled apart from the serializer because promotion is a
    # two-row operation, not a field assignment.
    if str(request.data.get("is_primary", "")).lower() in ("true", "1"):
        services.set_primary_image(image)
        image.refresh_from_db()

    services.schedule_event_details_notification(event, _gallery_change_description(event_day))
    return Response(
        EventImageSerializer(image, context={"request": request}).data,
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reorder_event_gallery(request, event_slug):
    """
    POST — set the display order of a whole gallery in one call:
    `{"image_ids": ["<uuid>", "<uuid>", ...]}`, position in the list becoming
    sort_order.

    One endpoint rather than N PATCHes because a drag-and-drop reorder changes
    every position at once: sent one at a time the gallery passes through
    orders that are briefly wrong, and a failure halfway leaves it scrambled
    with no way to tell which half applied. Every id must belong to the same
    gallery, so a partial or padded list is a 400 rather than a silent no-op.
    """
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        return _error("Event with this title does not exist.", NOT_FOUND, status.HTTP_404_NOT_FOUND)

    enforce(can_access_event(request.user, event), "You don't have permission to modify this event")

    if not is_staff_or_superuser(request.user) and _event_details_locked_for_event(event):
        return _locked_error()

    event_day, error = _resolve_gallery_scope(event, request.data.get("event_day"))
    if error:
        return error

    image_ids = request.data.get("image_ids")
    if not isinstance(image_ids, list) or not image_ids:
        return _error(
            "Send `image_ids` as a non-empty list of image ids in the desired order.",
            VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
        )

    gallery = {str(img.id): img for img in _gallery_queryset(event, event_day)}
    submitted = [str(i) for i in image_ids]

    if set(submitted) != set(gallery) or len(submitted) != len(gallery):
        return _error(
            "`image_ids` must list every image in this gallery exactly once.",
            VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        for position, image_id in enumerate(submitted):
            image = gallery[image_id]
            image.sort_order = position
            image.save(update_fields=["sort_order", "updated_at"])

    services.schedule_event_details_notification(event, _gallery_change_description(event_day))
    return Response(
        EventImageSerializer(
            _gallery_queryset(event, event_day), many=True, context={"request": request},
        ).data,
        status=status.HTTP_200_OK,
    )
